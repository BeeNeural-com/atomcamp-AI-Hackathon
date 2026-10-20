"""Production paths: Cloud Tasks dispatch, private worker endpoints, migrations, JSON logs."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from beeproof.config import set_settings
from beeproof.providers import queue as Q


class FakeTasksClient:
    def __init__(self, fail: bool = False):
        self.tasks: list[dict] = []
        self.fail = fail

    def create_task(self, request):
        if self.fail:
            raise RuntimeError("quota")
        self.tasks.append(request)
        return request

    def get_queue(self, name):
        return {"name": name}


def _tasks_queue(client=None) -> Q.CloudTasksQueue:
    return Q.CloudTasksQueue(
        "projects/p/locations/asia-south1/queues/beeproof-jobs",
        "https://beeproof-worker-xyz.a.run.app/",
        "tasks-invoker@p.iam.gserviceaccount.com",
        9999,
        client=client or FakeTasksClient(),
    )


def test_cloud_tasks_payload_is_authenticated_and_bounded():
    q = _tasks_queue()
    q.publish("job-1")
    q.publish("recovered")  # local sweep marker is not a job id
    (req,) = q.client.tasks
    http = req["task"]["http_request"]
    assert req["parent"].endswith("/queues/beeproof-jobs")
    assert http["url"] == "https://beeproof-worker-xyz.a.run.app/internal/jobs/job-1/run"
    assert http["oidc_token"] == {
        "service_account_email": "tasks-invoker@p.iam.gserviceaccount.com",
        "audience": "https://beeproof-worker-xyz.a.run.app",
    }
    assert req["task"]["dispatch_deadline"] == {"seconds": 1800}  # capped at the Cloud Tasks maximum
    assert q.ping() is True


def test_cloud_tasks_failure_is_swallowed():
    q = _tasks_queue(FakeTasksClient(fail=True))
    assert q.enqueue("/internal/sweep") is False


def test_cloud_tasks_requires_configuration():
    with pytest.raises(ValueError):
        Q.CloudTasksQueue("", "https://w", "sa@x")


@pytest.fixture
def worker_client(settings):
    from beeproof.worker_http import create_app

    with TestClient(create_app()) as c:
        yield c


def test_worker_endpoints(client, worker_client, sample_pdf, monkeypatch):
    q = _tasks_queue()
    monkeypatch.setattr(Q, "_queue", q)
    doc = client.post("/api/v1/documents", files={"file": ("t.pdf", sample_pdf, "application/pdf")}).json()["data"]
    job_id = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={}).json()["data"]["job_id"]
    assert q.client.tasks[-1]["task"]["http_request"]["url"].endswith(f"/internal/jobs/{job_id}/run")

    assert worker_client.get("/healthz").json() == {"status": "ok"}
    r = worker_client.post(f"/internal/jobs/{job_id}/run", headers={"X-CloudTasks-TaskRetryCount": "0"})
    assert r.json() == {"job_id": job_id, "status": "processed"}
    # duplicate delivery of the same task is a no-op
    assert worker_client.post(f"/internal/jobs/{job_id}/run").json()["status"] == "skipped"
    assert client.get(f"/api/v1/jobs/{job_id}").json()["data"]["status"] == "review_required"
    # private worker exposes no API docs
    assert worker_client.get("/docs").status_code == 404


def test_sweep_reenqueues_lost_deliveries(client, worker_client, sample_pdf, monkeypatch):
    from datetime import datetime, timedelta

    from beeproof.db import session_scope
    from beeproof.orm import ExtractionJob

    q = _tasks_queue()
    monkeypatch.setattr(Q, "_queue", q)
    doc = client.post("/api/v1/documents", files={"file": ("t.pdf", sample_pdf, "application/pdf")}).json()["data"]
    job_id = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={}).json()["data"]["job_id"]
    with session_scope() as s:
        s.get(ExtractionJob, job_id).created_at = datetime.now(UTC) - timedelta(minutes=10)
    q.client.tasks.clear()
    assert worker_client.post("/internal/sweep").json() == {"recovered": 0, "reenqueued": 1}
    assert q.client.tasks[0]["task"]["http_request"]["url"].endswith(f"/internal/jobs/{job_id}/run")


def test_evaluation_dispatched_to_worker(client, settings, monkeypatch):
    q = _tasks_queue()
    monkeypatch.setattr(Q, "_queue", q)
    r = client.post("/api/v1/evaluations", json={"configurations": ["native"]})
    assert r.status_code == 202
    eid = r.json()["data"]["id"]
    assert q.client.tasks[-1]["task"]["http_request"]["url"].endswith(f"/internal/evaluations/{eid}/run")
    assert client.get(f"/api/v1/evaluations/{eid}").json()["data"]["status"] == "queued"


def test_migrations_build_the_schema(settings, tmp_path):
    from sqlalchemy import create_engine, inspect

    from beeproof import migrate

    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    set_settings(replace(settings, database_url=url))
    migrate.run()
    migrate.run()  # idempotent
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"documents", "extraction_jobs", "extracted_fields", "alembic_version"} <= tables


def test_json_log_format(capsys):
    from beeproof import logging_setup

    logging_setup.configure("json")
    logging_setup.request_id_var.set("req-1")
    logging_setup.trace_var.set(logging_setup.trace_from_header("abc123/1;o=1", "proj"))
    logging.getLogger("beeproof.test").warning("hello")
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["severity"] == "WARNING" and line["request_id"] == "req-1"
    assert line["logging.googleapis.com/trace"] == "projects/proj/traces/abc123"
    logging_setup.configure("text")
