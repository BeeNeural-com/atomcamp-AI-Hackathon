"""Private worker service for Cloud Run (push delivery from Cloud Tasks / Cloud Scheduler).

Deployed as `beeproof-worker` with ingress=internal and no unauthenticated access:
only the Cloud Tasks / Scheduler invoker service account (OIDC) can call it.

* POST /internal/jobs/{job_id}/run          - claim one job atomically and run the pipeline
* POST /internal/evaluations/{run_id}/run   - run a benchmark evaluation
* POST /internal/sweep                       - recover stale jobs, re-enqueue lost deliveries
* GET  /healthz                              - readiness (database reachable)

Handlers always answer 200 once the job state is recorded (success, review, failure,
transient re-queue): retries are driven by the job state machine, not by Cloud Tasks
re-delivering a request that already did work. A 5xx is returned only when the job
state could not be read or written, so Cloud Tasks retries with backoff.
"""

from __future__ import annotations

import logging
import os
import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from beeproof import logging_setup
from beeproof.config import get_settings
from beeproof.db import get_engine, init_db, session_scope
from beeproof.providers.queue import get_queue
from beeproof.services import evaluation as eval_svc
from beeproof.services import schemas as schema_svc
from beeproof.services.jobs import claim_job, queued_job_ids, recover_stale
from beeproof.worker import Worker

log = logging.getLogger("beeproof.worker_http")
REDELIVER_AFTER_SECONDS = 120


def create_app() -> FastAPI:
    settings = get_settings()
    logging_setup.configure(settings.log_format)
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(settings)
        with session_scope() as s:
            schema_svc.ensure_builtin_schema(s)
        state["worker"] = Worker(settings)
        state["worker_id"] = f"{socket.gethostname()}-{os.getpid()}"
        yield

    app = FastAPI(title="BeeProof worker (private)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        task = request.headers.get("X-CloudTasks-TaskName")
        logging_setup.request_id_var.set(task or request.headers.get("X-Request-ID"))
        logging_setup.trace_var.set(
            logging_setup.trace_from_header(request.headers.get("X-Cloud-Trace-Context"), settings.project)
        )
        return await call_next(request)

    @app.get("/healthz")
    def healthz():
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"status": "ok"}
        except Exception:
            return JSONResponse({"status": "degraded"}, status_code=503)

    @app.post("/internal/jobs/{job_id}/run")
    def run_job(job_id: str, request: Request):
        retry = request.headers.get("X-CloudTasks-TaskRetryCount", "0")
        with session_scope() as s:
            claimed = claim_job(s, job_id, state["worker_id"])
        if not claimed:  # duplicate delivery, cancelled, or already done
            log.info("job %s not claimable (delivery retry %s); skipping", job_id, retry)
            return {"job_id": job_id, "status": "skipped"}
        state["worker"].process(job_id)  # records success / review / failure / re-queue itself
        return {"job_id": job_id, "status": "processed"}

    @app.post("/internal/evaluations/{run_id}/run")
    def run_evaluation(run_id: str):
        eval_svc.execute(settings, run_id)
        return {"evaluation_id": run_id, "status": "finished"}

    @app.post("/internal/sweep")
    def sweep():
        queue = get_queue(settings)
        with session_scope() as s:
            recovered = recover_stale(s, settings.stale_job_seconds)
        with session_scope() as s:
            pending = queued_job_ids(s, REDELIVER_AFTER_SECONDS)
        for job_id in pending:
            queue.publish(job_id)
        if recovered or pending:
            log.warning("sweep: recovered %d stale job(s), re-enqueued %d queued job(s)", recovered, len(pending))
        return {"recovered": recovered, "reenqueued": len(pending)}

    return app


app = create_app()
