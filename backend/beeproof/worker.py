"""Job worker: claims queued jobs from the database and runs the pipeline.

Delivery: Redis (QUEUE_URL) wakes the worker as soon as a job is queued; without
Redis the worker polls. Either way the job row in the database is the source of
truth and claims are atomic conditional UPDATEs, so a duplicate wake-up never
processes a job twice and a lost message is picked up by the periodic sweep.
Several workers can run in parallel.

Run standalone with `python -m beeproof.worker`, or inline inside the API
process (WORKER_MODE=inline, the default for local use).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

from beeproof import logging_setup
from beeproof.config import Settings, get_settings
from beeproof.db import init_db, session_scope
from beeproof.domain.states import JobStatus
from beeproof.pipeline.orchestrator import Pipeline
from beeproof.pipeline.triage import TriageError
from beeproof.providers.extractors import build_extractor
from beeproof.providers.layout import LayoutWitness, ProviderError
from beeproof.providers.queue import PollingQueue, Queue, get_queue
from beeproof.providers.storage import get_storage
from beeproof.services import memory
from beeproof.services.errors import AppError
from beeproof.services.jobs import claim_next, get_job, log_action, recover_stale, settings_for_job, transition
from beeproof.services.results import persist_result

log = logging.getLogger("beeproof.worker")


class JobCancelled(Exception):
    pass


class Worker:
    SWEEP_SECONDS = 5  # with Redis: max wait before re-checking the database anyway

    def __init__(self, settings: Settings, extractor=None, layout=None, queue: Queue | None = None):
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._extractor = extractor
        self._layout = layout
        self.queue = queue or get_queue(settings)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="beeproof-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def run_forever(self) -> None:
        log.info("worker %s started (queue: %s)", self.worker_id, self.queue.name)
        last_recovery = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_recovery > 30:
                    with session_scope() as s:
                        n = recover_stale(s, self.settings.stale_job_seconds)
                    if n:
                        log.warning("recovered %d stale job(s)", n)
                        self.queue.publish("recovered")
                    last_recovery = time.monotonic()
                if self.run_once():
                    continue  # drain queued jobs before waiting
                if isinstance(self.queue, PollingQueue):
                    self._stop.wait(self.settings.worker_poll_seconds)
                else:
                    self.queue.wait(self.SWEEP_SECONDS)  # wakes immediately on a published job id
            except Exception:  # keep the loop alive; the job itself records its failure
                log.exception("worker loop error")
                self._stop.wait(self.settings.worker_poll_seconds)

    def run_once(self) -> bool:
        with session_scope() as s:
            job_id = claim_next(s, self.worker_id)
        if job_id is None:
            return False
        self.process(job_id)
        return True

    # ------------------------------------------------------------ processing
    def _providers(self):
        if self._extractor is None:
            self._extractor = build_extractor(self.settings)
        if self._layout is None:
            self._layout = LayoutWitness(self.settings)
        return self._extractor, self._layout

    def process(self, job_id: str) -> None:
        with session_scope() as s:
            job = get_job(s, job_id)
            data = get_storage(self.settings).get(job.document.storage_key)
            filename = job.document.original_filename
            job_settings = settings_for_job(self.settings, job)
            log_action(
                s,
                job,
                "preprocessing",
                "supervisor",
                "claim",
                "JOB_CLAIMED",
                f"claimed by {self.worker_id} (attempt {job.attempt_count}/{job.max_attempts})",
            )

        def on_stage(stage: str) -> None:
            with session_scope() as s:
                j = get_job(s, job_id)
                if j.cancel_requested:
                    raise JobCancelled()
                transition(j, stage)

        try:
            extractor, layout = self._providers()
            pipeline = Pipeline(job_settings, extractor=extractor, layout=layout, hints=memory.hints_for)
            result = pipeline.run(data, filename, on_stage=on_stage)
            with session_scope() as s:
                job = get_job(s, job_id)
                if job.cancel_requested:
                    raise JobCancelled()
                transition(job, JobStatus.VALIDATING)
                persist_result(s, job_settings, job, result)
            log.info("job %s done: %d records", job_id, len(result.records))
        except JobCancelled:
            self._finish(job_id, JobStatus.CANCELLED, None, "cancelled at a stage boundary")
        except TriageError as exc:
            self._finish(job_id, JobStatus.FAILED, exc.code, exc.message)
        except ProviderError as exc:
            self._provider_failure(job_id, exc)
        except AppError as exc:
            self._finish(job_id, JobStatus.FAILED, exc.code, exc.message)
        except Exception as exc:
            log.exception("job %s crashed", job_id)
            self._finish(job_id, JobStatus.FAILED, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")

    def _provider_failure(self, job_id: str, exc: ProviderError) -> None:
        with session_scope() as s:
            job = get_job(s, job_id)
            if exc.transient and job.attempt_count < job.max_attempts:
                transition(job, JobStatus.QUEUED)
                job.error_code, job.error_message = exc.code, str(exc)
                log_action(
                    s,
                    job,
                    "queued",
                    "supervisor",
                    "retry",
                    exc.code,
                    f"transient provider error; re-queued: {exc}",
                    status="retrying",
                )
                requeued = True
            else:
                requeued = False
        if requeued:
            self.queue.publish(job_id)
            return
        self._finish(job_id, JobStatus.FAILED, exc.code, str(exc))

    def _finish(self, job_id: str, status: JobStatus, code: str | None, message: str) -> None:
        with session_scope() as s:
            job = get_job(s, job_id)
            transition(job, status)
            job.error_code = code
            job.error_message = message if code else None
            log_action(
                s,
                job,
                status.value,
                "supervisor",
                "finish",
                code or status.value.upper(),
                message,
                status="failed" if status == JobStatus.FAILED else "succeeded",
            )


def main() -> None:
    logging_setup.configure(get_settings().log_format)
    settings = get_settings()
    init_db(settings)
    worker = Worker(settings)
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
