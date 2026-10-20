"""API contract tests against the frontend wire types (lib/types.ts) and handoff checklist."""

from __future__ import annotations

import csv
import io
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from beeproof.config import set_settings
from beeproof.providers.layout import ProviderError
from tests.conftest import make_rate_sheet

FIELDS = ["institution_name", "product_type", "term_months", "rate_pct", "apy_pct", "min_deposit", "effective_date"]


def _upload(client, pdf: bytes, name: str = "test_federal_bank_rates_page1.pdf"):
    r = client.post("/api/v1/documents", files={"file": (name, pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _job(client, doc_id: str, **body):
    r = client.post(f"/api/v1/documents/{doc_id}/jobs", json=body)
    assert r.status_code in (200, 202), r.text
    return r.json()["data"]["job_id"]


def _processed(client, run_worker, pdf: bytes):
    doc = _upload(client, pdf)
    job_id = _job(client, doc["id"])
    assert run_worker() is True
    return doc, job_id, client.get(f"/api/v1/jobs/{job_id}/results").json()["data"]


def _review(client, field_id: str, version: int, **body):
    payload = {
        "reason_code": "TEST",
        "comment": "",
        "reviewer_name": "",
        "evidence_ids": [],
        "expected_result_version": version,
        "corrected_value": None,
        **body,
    }
    return client.post(f"/api/v1/fields/{field_id}/review", json=payload)


def test_health_reports_open_access(client):
    body = client.get("/api/v1/health").json()
    assert body["data"]["access"] == "open" and body["meta"]["request_id"]


def test_upload_validation_errors(client):
    r = client.post("/api/v1/documents", files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    r = client.post("/api/v1/documents", files={"file": ("a.png", make_rate_sheet([]), "application/pdf")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_FILE"
    r = client.post(
        "/api/v1/documents", files={"file": ("../../etc/passwd.pdf", make_rate_sheet([]), "application/pdf")}
    )
    assert r.status_code == 201 and r.json()["data"]["original_filename"] == "passwd.pdf"


def test_file_too_large(client, settings):
    set_settings(replace(settings, max_upload_bytes=100))
    r = client.post("/api/v1/documents", files={"file": ("a.pdf", make_rate_sheet([]), "application/pdf")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_document_item_shape(client, sample_pdf, run_worker):
    doc = _upload(client, sample_pdf)
    assert (
        doc["job_id"],
        doc["status"],
        doc["record_count"],
        doc["result_version"],
        doc["institution_name"],
        doc["is_demo"],
    ) == ("", "queued", 0, 0, "", False)
    job_id = _job(client, doc["id"])
    run_worker()
    listed = client.get("/api/v1/documents", params={"limit": 200}).json()["data"]
    item = next(d for d in listed if d["id"] == doc["id"])
    assert item["job_id"] == job_id and item["status"] == "review_required"
    assert item["record_count"] == 3 and item["result_version"] == 1
    assert item["institution_name"] == "Test Federal Bank"
    page = client.get(f"/api/v1/documents/{doc['id']}/pages").json()["data"][0]
    img = client.get(page["preview_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


def test_schemas_are_published_versions(client):
    versions = client.get("/api/v1/schemas").json()["data"]
    v = versions[0]
    assert v["status"] == "published" and v["name"] == "Deposit rate sheet"
    assert [f["name"] for f in v["fields"]] == FIELDS
    assert all(f["description"] for f in v["fields"])
    assert {f["name"] for f in v["fields"] if not f["required"]} == {"rate_pct", "apy_pct", "min_deposit"}


def test_results_wire_shape(client, sample_pdf, run_worker):
    doc, job_id, results = _processed(client, run_worker, sample_pdf)
    job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
    assert job["progress"] is None and job["status"] == "review_required" and job["result_version"] == 1
    assert results["result_version"] == 1 and len(results["records"]) == 3
    cd = next(r for r in results["records"] if r["values"]["product_type"] == "CD")
    assert set(cd["values"]) == set(FIELDS) and set(cd["fields"]) == set(FIELDS)
    assert cd["document_id"] == doc["id"] and cd["job_id"] == job_id and cd["page"] == 1 and cd["version"] == 1
    assert cd["status"] == "validated" and cd["extraction_confidence"] == "1.00"
    assert cd["values"]["rate_pct"] == "3.097" and cd["values"]["min_deposit"] == "1000.00"
    apy = cd["fields"]["apy_pct"]
    assert apy["field_name"] == "apy_pct" and apy["review_status"] == "accepted"
    assert apy["witness_a"] == "3.119%" and apy["witness_b"] == "3.119%"
    ev = apy["evidence"][0]
    assert ev["source_type"] == "native_text" and ev["bounding_box"]["coordinate_space"] == "normalized_page"
    assert client.get(f"/api/v1/evidence/{ev['id']}").json()["data"]["page_number"] == 1
    assert {v["severity"] for v in cd["validations"]} <= {"error", "warning", "info"}
    mm = next(r for r in results["records"] if r["values"]["product_type"] == "Money Market")
    assert mm["status"] == "needs_review" and mm["decision"] == "DOCUMENT_INCONSISTENCY"
    assert any(
        v["rule_id"] == "APY_PHYSICS_MAX" and v["status"] == "fail" and v["severity"] == "error"
        for v in mm["validations"]
    )

    trace = client.get(f"/api/v1/jobs/{job_id}/trace").json()["data"]
    assert all(t["status"] in {"success", "warning", "error"} and t["job_id"] == job_id for t in trace)
    relook = next(t for t in trace if t["action_type"] == "reextract_region")
    assert client.get(relook["artifact_urls"][0]).headers["content-type"] == "image/png"
    fails = client.get(f"/api/v1/jobs/{job_id}/validation", params={"status": "fail"}).json()["data"]
    assert {f["rule_id"] for f in fails if f["severity"] == "error"} == {"APY_PHYSICS_MAX"}
    assert {f["severity"] for f in fails if f["rule_id"] == "COMPOUNDING_MATCH"} == {"warning"}


def test_review_rules(client, sample_pdf, run_worker):
    doc, job_id, results = _processed(client, run_worker, sample_pdf)
    mm = next(r for r in results["records"] if r["values"]["product_type"] == "Money Market")
    sv = next(r for r in results["records"] if r["values"]["product_type"] == "Savings")
    apy_id, rate_id = mm["fields"]["apy_pct"]["id"], mm["fields"]["rate_pct"]["id"]

    # stale version -> 409
    r = _review(client, apy_id, 99, decision="accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_RESULT_VERSION"
    # accepting a value that fails a hard rule -> 422
    r = _review(client, apy_id, 1, decision="accept")
    assert r.status_code == 422 and r.json()["error"]["code"] == "MANDATORY_VALIDATION_FAILED"
    # correction that is out of range -> 422; correction that still breaks physics -> 422
    assert _review(client, apy_id, 1, decision="correct", corrected_value="42%").status_code == 422
    assert _review(client, apy_id, 1, decision="correct", corrected_value="1.50").status_code == 422
    # foreign evidence -> 422
    r = _review(client, apy_id, 1, decision="correct", corrected_value="1.081", evidence_ids=["nope"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "EVIDENCE_NOT_FOUND"

    # valid correction: new full snapshot, physics passes, the rate's reason goes away
    own_ev = mm["fields"]["apy_pct"]["evidence"][0]["id"]
    r = _review(
        client,
        apy_id,
        1,
        decision="correct",
        corrected_value="1.081%",
        reason_code="SOURCE_CORRECTION",
        reviewer_name="qa",
        evidence_ids=[own_ev],
    )
    assert r.status_code == 200, r.text
    new = r.json()["data"]
    assert new["result_version"] == 2 and len(new["records"]) == 3
    mm2 = next(x for x in new["records"] if x["id"] == mm["id"])
    assert mm2["values"]["apy_pct"] == "1.081" and mm2["status"] == "human_verified"
    assert mm2["fields"]["apy_pct"]["raw_value"] == "1.162%"  # raw source is immutable
    assert mm2["fields"]["rate_pct"]["review_status"] == "accepted"
    assert all(x["version"] == 2 for x in new["records"])

    history = client.get(f"/api/v1/fields/{apy_id}/history").json()["data"]
    assert history[0]["previous_value"] == "1.162" and history[0]["corrected_value"] == "1.081"
    assert history[0]["expected_result_version"] == 1 and history[0]["result_version"] == 2
    assert client.get(f"/api/v1/fields/{rate_id}/history").json()["data"] == []

    # the Savings record can only be corrected or rejected; rejecting both fields completes the job
    for name in ("apy_pct", "rate_pct"):
        version = client.get(f"/api/v1/jobs/{job_id}").json()["data"]["result_version"]
        assert _review(client, sv["fields"][name]["id"], version, decision="reject").status_code == 200
    job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
    assert job["status"] == "completed"
    final = client.get(f"/api/v1/jobs/{job_id}/results").json()["data"]
    assert next(x for x in final["records"] if x["id"] == sv["id"])["status"] == "rejected"

    # exports: old versions stay immutable; rejected rows still need attention
    v1 = client.post(f"/api/v1/jobs/{job_id}/exports", json={"format": "csv", "result_version": 1}).json()["data"]
    assert v1["status"] == "ready" and v1["document_name"] == doc["original_filename"]
    latest = client.post(f"/api/v1/jobs/{job_id}/exports", json={"format": "csv"}).json()["data"]
    raw_v1 = client.get(f"/api/v1/exports/{v1['id']}/download").content.decode()
    assert "\r\n" in raw_v1
    rows_v1 = list(csv.DictReader(io.StringIO(raw_v1)))
    rows_latest = list(csv.DictReader(io.StringIO(client.get(f"/api/v1/exports/{latest['id']}/download").text)))
    assert list(rows_v1[0])[:13] == [
        "source_file",
        *FIELDS,
        "extraction_confidence",
        "needs_review",
        "decision",
        "record_id",
        "result_version",
    ]
    assert next(r for r in rows_v1 if r["product_type"] == "Money Market")["apy_pct"] == "1.162"
    assert next(r for r in rows_latest if r["product_type"] == "Money Market")["apy_pct"] == "1.081"
    assert next(r for r in rows_latest if r["product_type"] == "Savings")["needs_review"] == "True"
    js = client.post(f"/api/v1/jobs/{job_id}/exports", json={"format": "json", "include_evidence_manifest": True})
    assert "evidence_manifest" in client.get(f"/api/v1/exports/{js.json()['data']['id']}/download").json()
    r = client.post(f"/api/v1/jobs/{job_id}/exports", json={"format": "csv", "result_version": 42})
    assert r.status_code == 404 and r.json()["error"]["code"] == "RESULT_VERSION_NOT_FOUND"


def test_csv_neutralizes_formulas():
    from beeproof.services.exports import _csv

    rec = {
        "source_file": '=HYPERLINK("x")',
        "values": {n: None for n in FIELDS},
        "extraction_confidence": "0.50",
        "status": "needs_review",
        "decision": "NEEDS_REVIEW",
        "id": "r1",
        "review_reasons": ["@SUM(A1)"],
        "inferred_compounding": None,
        "page": 1,
    }
    rec["values"]["institution_name"] = "+Evil Bank"
    rec["values"]["rate_pct"] = "-0.5"
    row = next(csv.DictReader(io.StringIO(_csv({"records": [rec], "result_version": 3}).decode())))
    assert row["source_file"].startswith("'=") and row["institution_name"] == "'+Evil Bank"
    assert row["review_reasons"] == "'@SUM(A1)" and row["rate_pct"] == "-0.5"


def test_idempotency_cancel_and_retry_new_run(client, sample_pdf, run_worker):
    doc = _upload(client, sample_pdf)
    job_id = _job(client, doc["id"], idempotency_key="abc")
    replay = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={"idempotency_key": "abc"})
    assert replay.status_code == 200 and replay.json()["data"]["job_id"] == job_id
    other = _upload(client, sample_pdf)
    assert client.post(f"/api/v1/documents/{other['id']}/jobs", json={"idempotency_key": "abc"}).status_code == 409
    assert client.post(f"/api/v1/jobs/{job_id}/retry", json={}).json()["error"]["code"] == "JOB_STATE_CONFLICT"
    assert client.post(f"/api/v1/jobs/{job_id}/cancel", json={}).json()["data"]["status"] == "cancelled"
    assert run_worker() is False or client.get(f"/api/v1/jobs/{job_id}").json()["data"]["status"] == "cancelled"
    new = client.post(f"/api/v1/jobs/{job_id}/retry", json={}).json()["data"]
    assert new["id"] != job_id and new["status"] == "queued" and new["retry_of"] == job_id
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["data"]["job_id"] == new["id"]


class FailingExtractor:
    name = "always-down"

    def extract(self, ctx, hints=""):
        raise ProviderError("PROVIDER_TIMEOUT", "upstream timeout", transient=True)

    def relook(self, *a, **k):  # pragma: no cover
        raise AssertionError


def test_transient_failures_retry_then_fail(client, sample_pdf, run_worker):
    doc = _upload(client, sample_pdf)
    job_id = _job(client, doc["id"])
    for _ in range(3):
        assert run_worker(extractor=FailingExtractor()) is True
    job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
    assert job["status"] == "failed" and job["error_code"] == "PROVIDER_TIMEOUT" and job["attempt_count"] == 3
    new = client.post(f"/api/v1/jobs/{job_id}/retry", json={}).json()["data"]
    assert run_worker() is True
    assert client.get(f"/api/v1/jobs/{new['id']}").json()["data"]["status"] == "review_required"


def test_stale_job_recovery(client, sample_pdf):
    from beeproof.db import session_scope
    from beeproof.orm import ExtractionJob
    from beeproof.services.jobs import claim_next, recover_stale

    doc = _upload(client, sample_pdf)
    job_id = _job(client, doc["id"])
    with session_scope() as s:
        assert claim_next(s, "dead-worker") == job_id
    with session_scope() as s:
        s.get(ExtractionJob, job_id).heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    with session_scope() as s:
        assert recover_stale(s, 60) == 1
    assert client.get(f"/api/v1/jobs/{job_id}").json()["data"]["status"] == "queued"


def test_unsupported_schema_rejected(client):
    created = client.post(
        "/api/v1/schemas", json={"name": "custom", "schema_json": {"fields": [{"name": "x", "type": "string"}]}}
    )
    assert created.status_code == 201
    version_id = created.json()["data"]["versions"][0]["id"]
    doc = _upload(client, make_rate_sheet([]))
    r = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={"schema_version_id": version_id})
    assert r.status_code == 422 and r.json()["error"]["code"] == "SCHEMA_NOT_SUPPORTED"
    r = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={"schema_version_id": "missing"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "SCHEMA_NOT_FOUND"


def test_delete_document(client, sample_pdf):
    doc = _upload(client, sample_pdf)
    assert client.delete(f"/api/v1/documents/{doc['id']}").json()["data"]["deleted"] is True
    assert client.get(f"/api/v1/documents/{doc['id']}").status_code == 404


def test_validation_error_envelope(client, sample_pdf):
    doc = _upload(client, sample_pdf)
    r = client.post(f"/api/v1/documents/{doc['id']}/jobs", json={"options": {"unknown": True}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_rate_limit(settings, sample_pdf):
    from fastapi.testclient import TestClient

    from beeproof.api.app import create_app

    set_settings(replace(settings, rate_limit_per_min=2))
    with TestClient(create_app(start_worker=False)) as c:
        codes = [
            c.post("/api/v1/documents", files={"file": ("a.pdf", sample_pdf, "application/pdf")}).status_code
            for _ in range(3)
        ]
        assert codes == [201, 201, 429]
        assert c.get("/api/v1/documents").status_code == 200  # reads are not limited


def test_evaluation_report_contract(client, settings):
    from beeproof.config import REPO_DIR
    from bench.generate import generate

    generate(REPO_DIR / "ps06_sample_rate_sheets.csv", settings.bench_dir, inject=0.0, images=False, limit=3)
    r = client.post(
        "/api/v1/evaluations",
        json={"dataset_version": "ps06-synthetic-v1", "configurations": ["native", "single_pass", "evidence_first"]},
    )
    assert r.status_code == 202, r.text
    rid = r.json()["data"]["id"]
    for _ in range(120):
        report = client.get(f"/api/v1/evaluations/{rid}/report").json()["data"]
        if report["status"] in {"completed", "failed"}:
            break
        time.sleep(0.25)
    assert report["status"] == "completed", report
    pipelines = {p["name"]: p for p in report["pipelines"]}
    assert pipelines["single_pass"]["field_accuracy"] is None and pipelines["single_pass"]["skipped"]
    assert pipelines["evidence_first"]["evidence_coverage"] == 1.0
    assert pipelines["evidence_first"]["false_auto_accept_rate"] == 0.0
    assert report["document_count"] == 3 and report["field_count"] > 0
    acc = next(m for m in report["metrics"] if m["name"] == "field_accuracy")
    assert acc["denominator"] == report["field_count"]
    md = client.get(f"/api/v1/evaluations/{rid}/report", params={"format": "md"})
    assert md.status_code == 200 and "# BeeProof benchmark report" in md.text
