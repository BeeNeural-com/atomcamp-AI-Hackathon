"""Job delivery queue.

The database row is the durable source of truth for a job; the queue only carries
job-id wake-up messages (ARCHITECTURE.md: "queue messages contain identifiers").
Workers always claim jobs with an atomic conditional UPDATE, so delivery is
at-least-once and duplicate or lost messages are harmless: a duplicate finds the
job already claimed, and a lost one is picked up by the periodic database sweep.

* CloudTasksQueue - production: one authenticated HTTP task per job, dispatched by
  Google Cloud Tasks to the private worker service (CLOUD_TASKS_QUEUE). Cloud Tasks
  applies rate limits / max concurrency (backpressure for AI quotas) and bounded retries.
* RedisQueue - LPUSH / BRPOP on a Redis list (QUEUE_URL=redis://...), used by docker compose.
* PollingQueue - no broker; workers poll the database (local dev / tests).
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from beeproof.config import Settings

log = logging.getLogger(__name__)
JOBS_KEY = "beeproof:jobs"


class Queue(Protocol):
    name: str

    def publish(self, job_id: str) -> None: ...

    def wait(self, timeout: float) -> str | None: ...

    def ping(self) -> bool: ...


class PollingQueue:
    name = "database-poll"

    def publish(self, job_id: str) -> None:
        return None

    def wait(self, timeout: float) -> str | None:
        time.sleep(timeout)
        return None

    def ping(self) -> bool:
        return True


class RedisQueue:
    name = "redis"

    def __init__(self, url: str, client=None):
        if client is None:
            import redis  # lazy: only needed when QUEUE_URL is set

            client = redis.Redis.from_url(
                url, socket_connect_timeout=3, socket_timeout=None, health_check_interval=30, decode_responses=True
            )
        self.client = client

    def publish(self, job_id: str) -> None:
        try:
            self.client.lpush(JOBS_KEY, job_id)
        except Exception as exc:  # the DB sweep still delivers the job
            log.warning("queue publish failed for %s: %s", job_id, exc)

    def wait(self, timeout: float) -> str | None:
        try:
            item = self.client.brpop([JOBS_KEY], timeout=max(1, int(timeout)))
        except Exception as exc:
            log.warning("queue wait failed: %s", exc)
            time.sleep(min(timeout, 5))
            return None
        return item[1] if item else None

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False


class CloudTasksQueue:
    """HTTP push delivery through Google Cloud Tasks with OIDC authentication."""

    name = "cloud-tasks"

    def __init__(
        self,
        queue_path: str,
        worker_url: str,
        invoker_service_account: str,
        dispatch_deadline_seconds: int = 1800,
        client=None,
    ):
        if not (queue_path and worker_url and invoker_service_account):
            raise ValueError("CLOUD_TASKS_QUEUE, WORKER_SERVICE_URL and TASKS_INVOKER_SERVICE_ACCOUNT are required")
        if client is None:
            from google.cloud import tasks_v2  # lazy: optional dependency

            client = tasks_v2.CloudTasksClient()
        self.client = client
        self.queue_path = queue_path
        self.worker_url = worker_url.rstrip("/")
        self.invoker = invoker_service_account
        self.deadline = min(max(dispatch_deadline_seconds, 15), 1800)  # Cloud Tasks HTTP limit: 30 min

    def task(self, path: str) -> dict:
        return {
            "http_request": {
                "http_method": "POST",
                "url": f"{self.worker_url}{path}",
                "headers": {"Content-Type": "application/json"},
                "body": b"{}",
                "oidc_token": {"service_account_email": self.invoker, "audience": self.worker_url},
            },
            "dispatch_deadline": {"seconds": self.deadline},
        }

    def enqueue(self, path: str) -> bool:
        try:
            self.client.create_task(request={"parent": self.queue_path, "task": self.task(path)})
            return True
        except Exception as exc:  # the scheduled sweep re-enqueues queued jobs
            log.warning("cloud tasks enqueue failed for %s: %s", path, exc)
            return False

    def publish(self, job_id: str) -> None:
        if job_id and not job_id.startswith("recovered"):
            self.enqueue(f"/internal/jobs/{job_id}/run")

    def wait(self, timeout: float) -> str | None:
        time.sleep(timeout)  # push delivery: workers do not poll
        return None

    def ping(self) -> bool:
        try:
            self.client.get_queue(name=self.queue_path)
            return True
        except Exception:
            return False


_queue: Queue | None = None


def get_queue(settings: Settings) -> Queue:
    global _queue
    if _queue is None:
        if settings.cloud_tasks_queue:
            _queue = CloudTasksQueue(
                settings.cloud_tasks_queue,
                settings.worker_service_url,
                settings.tasks_invoker_service_account,
                settings.tasks_dispatch_deadline_seconds,
            )
        elif settings.queue_url.startswith("redis"):
            _queue = RedisQueue(settings.queue_url)
        else:
            _queue = PollingQueue()
    return _queue


def reset_queue() -> None:
    global _queue
    _queue = None
