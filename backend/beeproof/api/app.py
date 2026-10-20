"""FastAPI application: /api/v1. Wire shapes follow the frontend's lib/types.ts
(see CLAUDE_BACKEND_HANDOFF.md). Open access - no authentication (ADR-004)."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from beeproof import logging_setup
from beeproof.api import models as M
from beeproof.config import PIPELINE_VERSION, get_settings
from beeproof.db import get_engine, get_session, init_db, session_scope
from beeproof.orm import (
    AgentAction,
    Artifact,
    Document,
    Evidence,
    ExportArtifact,
    ExtractedRecord,
    ExtractionJob,
    ValidationResult,
)
from beeproof.providers.queue import RedisQueue, get_queue
from beeproof.providers.storage import get_storage
from beeproof.services import documents as docs_svc
from beeproof.services import evaluation as eval_svc
from beeproof.services import exports as export_svc
from beeproof.services import jobs as jobs_svc
from beeproof.services import review as review_svc
from beeproof.services import schemas as schema_svc
from beeproof.services.errors import AppError, not_found
from beeproof.services.results import SEVERITY_WIRE, get_result_version, serialize_evidence, serialize_field

log = logging.getLogger("beeproof.api")
API = "/api/v1"
TRACE_STATUS = {
    "succeeded": "success",
    "failed": "error",
    "unresolved": "warning",
    "skipped": "warning",
    "retrying": "warning",
    "warning": "warning",
}


# ------------------------------------------------------------------ helpers
def rid(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def ok(request: Request, data, next_cursor: int | None = None, status_code: int | None = None):
    """Success envelope. An explicit status_code overrides the route default (e.g. 200 on idempotent replay)."""
    body = {
        "data": data,
        "meta": {"request_id": rid(request), "next_cursor": None if next_cursor is None else str(next_cursor)},
    }
    if status_code is not None:
        return JSONResponse(jsonable_encoder(body), status_code=status_code)
    return body


def page_params(limit: int = Query(50, ge=1, le=200), cursor: Optional[str] = Query(None)) -> tuple[int, int]:
    try:
        offset = int(cursor) if cursor else 0
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "cursor is invalid", 400) from exc
    return limit, max(offset, 0)


def document_out(session: Session, doc: Document) -> dict:
    """DocumentItem: the document projected through its latest run."""
    latest = max(doc.jobs, key=lambda j: j.created_at, default=None)
    institution, records = "", 0
    if latest is not None and latest.result_version:
        records = session.query(ExtractedRecord).filter(ExtractedRecord.job_id == latest.id).count()
        first = session.scalar(
            select(ExtractedRecord)
            .where(ExtractedRecord.job_id == latest.id)
            .order_by(ExtractedRecord.record_index)
            .limit(1)
        )
        if first is not None:
            institution = (first.data_json or {}).get("institution_name") or ""
    return {
        "id": doc.id,
        "original_filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "page_count": doc.page_count,
        "institution_name": institution,
        "created_at": doc.created_at,
        "job_id": latest.id if latest else "",
        "status": latest.status if latest else "queued",
        "record_count": records,
        "result_version": latest.result_version if latest else 0,
        "is_demo": False,
        "sha256": doc.sha256,
        "duplicate_of": (doc.metadata_json or {}).get("duplicate_of"),
    }


def page_out(p) -> dict:
    return {
        "page_number": p.page_number,
        "width": p.width,
        "height": p.height,
        "rotation": p.rotation,
        "kind": p.kind,
        "native_text_available": p.native_text_available,
        "layout_provider": p.layout_provider,
        "preview_url": f"{API}/documents/{p.document_id}/pages/{p.page_number}/preview",
    }


def job_out(session: Session, job: ExtractionJob) -> dict:
    summary = get_result_version(session, job, None)["summary"] if job.result_version else None
    cost = job.estimated_cost_usd
    return {
        "id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "stage": job.stage,
        "progress": None,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "trace_id": job.trace_id,
        "result_version": job.result_version,
        "schema_version_id": job.schema_version_id,
        "max_attempts": job.max_attempts,
        "retry_of": job.retry_of,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "pipeline_version": job.pipeline_version,
        "total_latency_ms": job.total_latency_ms,
        "estimated_cost_usd": None if cost is None else f"{cost:.6f}",
        "provider_calls": job.provider_calls,
        "cancel_requested": job.cancel_requested,
        "configuration_snapshot": job.configuration_snapshot or {},
        "summary": summary,
    }


def export_out(e: ExportArtifact) -> dict:
    ready = e.status == "ready"
    return {
        "id": e.id,
        "job_id": e.job_id,
        "document_name": e.job.document.original_filename if e.job else "",
        "result_version": e.result_version,
        "format": e.format,
        "status": e.status if e.status in {"queued", "processing", "ready", "failed"} else "processing",
        "created_at": e.created_at,
        "error_code": e.error_code,
        "include_evidence_manifest": e.include_evidence_manifest,
        "sha256": e.sha256,
        "size_bytes": e.size_bytes,
        "completed_at": e.completed_at,
        "download_url": f"{API}/exports/{e.id}/download" if ready else None,
        "manifest_url": (
            f"{API}/exports/{e.id}/download?part=manifest"
            if ready and e.format == "csv" and e.include_evidence_manifest
            else None
        ),
    }


def trace_out(a: AgentAction) -> dict:
    return {
        "id": a.id,
        "job_id": a.job_id,
        "stage": a.stage,
        "summary": a.summary,
        "status": TRACE_STATUS.get(a.result_status, "success"),
        "created_at": a.created_at,
        "duration_ms": a.duration_ms,
        "seq": a.seq,
        "trace_id": a.trace_id,
        "agent_name": a.agent_name,
        "action_type": a.action_type,
        "reason_code": a.reason_code,
        "input_refs": a.input_refs or [],
        "output_refs": a.output_refs or [],
        "artifact_urls": [
            f"{API}/artifacts/{r.split(':', 1)[1]}" for r in (a.output_refs or []) if r.startswith("artifact:")
        ],
        "attempt_number": a.attempt_number,
        "model_name": a.model_name,
        "token_usage": a.token_usage_json,
        "estimated_cost_usd": a.estimated_cost_usd,
    }


ERRORS = {
    400: {"model": M.ErrorEnvelope},
    404: {"model": M.ErrorEnvelope},
    409: {"model": M.ErrorEnvelope},
    422: {"model": M.ErrorEnvelope},
    429: {"model": M.ErrorEnvelope},
}

# ------------------------------------------------------------------ routers
health = APIRouter(tags=["health"])
documents = APIRouter(prefix=API, tags=["documents"], responses=ERRORS)
schemas = APIRouter(prefix=API, tags=["schemas"], responses=ERRORS)
jobs = APIRouter(prefix=API, tags=["jobs"], responses=ERRORS)
evidence = APIRouter(prefix=API, tags=["evidence"], responses=ERRORS)
review = APIRouter(prefix=API, tags=["review"], responses=ERRORS)
exports = APIRouter(prefix=API, tags=["exports"], responses=ERRORS)
evaluations = APIRouter(prefix=API, tags=["evaluations"], responses=ERRORS)


def _health() -> dict:
    s = get_settings()
    db = "sqlite" if s.database_url.startswith("sqlite") else s.database_url.split(":", 1)[0].split("+")[0]
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    queue = get_queue(s)
    queue_ok = queue.ping()
    return {
        "status": "ok" if db_ok and queue_ok else "degraded",
        "version": PIPELINE_VERSION,
        "extractor": s.extractor,
        "layout_provider": s.layout_provider,
        "store": s.store,
        "database": db,
        "database_ok": db_ok,
        "queue": queue.name,
        "queue_ok": queue_ok,
        "worker_mode": s.worker_mode,
        "access": "open",
        "revision": os.environ.get("K_REVISION"),
    }


@health.get("/healthz", response_model=M.HealthOut, responses={503: {"model": M.HealthOut}})
def healthz():
    """Readiness: 503 when the database or the queue is unreachable (used by Docker healthchecks)."""
    h = _health()
    return h if h["status"] == "ok" else JSONResponse(h, status_code=503)


@health.get(f"{API}/health", response_model=M.Envelope[M.HealthOut])
def health_v1(request: Request):
    return ok(request, _health())


# --- documents
@documents.post(
    "/documents",
    status_code=201,
    response_model=M.Envelope[M.DocumentItem],
    responses={413: {"model": M.ErrorEnvelope}, 415: {"model": M.ErrorEnvelope}},
)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    process: bool = Query(False, description="Also queue an extraction job with defaults"),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    data = await file.read(settings.max_upload_bytes + 1)
    doc = docs_svc.create_document(session, settings, file.filename, data, file.content_type)
    job = jobs_svc.create_job(session, settings, doc.id, None, {}, None)[0] if process else None
    session.commit()  # publish only after the job row is visible
    if job is not None:
        get_queue(settings).publish(job.id)
    session.refresh(doc)
    return ok(request, document_out(session, doc), status_code=201)


@documents.get("/documents", response_model=M.Envelope[list[M.DocumentItem]])
def list_documents(request: Request, page=Depends(page_params), session: Session = Depends(get_session)):
    rows, nxt = docs_svc.list_documents(session, *page)
    return ok(request, [document_out(session, d) for d in rows], nxt)


@documents.get("/documents/{document_id}", response_model=M.Envelope[M.DocumentItem])
def get_document(request: Request, document_id: str, session: Session = Depends(get_session)):
    return ok(request, document_out(session, docs_svc.get_document(session, document_id)))


@documents.get("/documents/{document_id}/pages", response_model=M.Envelope[list[M.PageOut]])
def get_pages(request: Request, document_id: str, session: Session = Depends(get_session)):
    return ok(request, [page_out(p) for p in docs_svc.get_document(session, document_id).pages])


@documents.get("/documents/{document_id}/pages/{page_number}/preview", responses={200: {"content": {"image/png": {}}}})
def get_preview(document_id: str, page_number: int, session: Session = Depends(get_session)):
    page = docs_svc.get_page(session, document_id, page_number)
    return Response(
        get_storage(get_settings()).get(page.preview_key),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@documents.delete("/documents/{document_id}", response_model=M.Envelope[dict])
def delete_document(request: Request, document_id: str, session: Session = Depends(get_session)):
    docs_svc.delete_document(session, get_settings(), document_id)
    return ok(request, {"id": document_id, "deleted": True})


# --- schemas
@schemas.get("/schemas", response_model=M.Envelope[list[M.SchemaVersionOut]])
def list_schema_versions(request: Request, session: Session = Depends(get_session)):
    """Published schema versions with fields inline (what the UI needs to start a job)."""
    return ok(request, [schema_svc.version_wire(v) for v in schema_svc.published_versions(session)])


@schemas.post("/schemas", status_code=201, response_model=M.Envelope[M.SchemaOut])
def create_schema(request: Request, body: M.SchemaCreate, session: Session = Depends(get_session)):
    s = schema_svc.create_schema(session, body.name, body.description, body.schema_json, body.business_rules_json)
    session.refresh(s)
    return ok(
        request,
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "created_at": s.created_at,
            "versions": [schema_svc.version_wire(v) for v in s.versions],
        },
        status_code=201,
    )


@schemas.get("/schemas/{schema_id}", response_model=M.Envelope[M.SchemaOut])
def get_schema(request: Request, schema_id: str, session: Session = Depends(get_session)):
    s = schema_svc.get_schema(session, schema_id)
    return ok(
        request,
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "created_at": s.created_at,
            "versions": [schema_svc.version_wire(v) for v in s.versions],
        },
    )


@schemas.get("/schemas/{schema_id}/versions", response_model=M.Envelope[list[M.SchemaVersionOut]])
def list_versions(request: Request, schema_id: str, session: Session = Depends(get_session)):
    return ok(request, [schema_svc.version_wire(v) for v in schema_svc.get_schema(session, schema_id).versions])


@schemas.post("/schemas/{schema_id}/versions", status_code=201, response_model=M.Envelope[M.SchemaVersionOut])
def create_version(
    request: Request, schema_id: str, body: M.SchemaVersionCreate, session: Session = Depends(get_session)
):
    v = schema_svc.create_version(session, schema_id, body.schema_json, body.business_rules_json, body.status)
    session.refresh(v)
    return ok(request, schema_svc.version_wire(v), status_code=201)


@schemas.post("/schemas/{schema_id}/validate", response_model=M.Envelope[M.SchemaValidateOut])
def validate_schema(
    request: Request, schema_id: str, body: M.SchemaValidateIn, session: Session = Depends(get_session)
):
    schema_svc.get_schema(session, schema_id)
    errs = schema_svc.validate_schema_json(body.schema_json)
    return ok(request, {"valid": not errs, "details": errs})


# --- jobs
@jobs.post("/documents/{document_id}/jobs", status_code=202, response_model=M.Envelope[M.JobCreated])
def create_job(
    request: Request, document_id: str, body: Optional[M.JobCreate] = None, session: Session = Depends(get_session)
):
    body = body or M.JobCreate()
    settings = get_settings()
    job, created = jobs_svc.create_job(
        session, settings, document_id, body.schema_version_id, body.options.model_dump(), body.idempotency_key
    )
    data = {"job_id": job.id, "status": job.status, "status_url": f"{API}/jobs/{job.id}"}
    if created:
        session.commit()  # publish only after the job row is visible
        get_queue(settings).publish(job.id)
    return ok(request, data, status_code=202 if created else 200)


@jobs.get("/jobs", response_model=M.Envelope[list[M.JobOut]])
def list_jobs(
    request: Request,
    document_id: Optional[str] = None,
    status: Optional[str] = None,
    page=Depends(page_params),
    session: Session = Depends(get_session),
):
    rows, nxt = jobs_svc.list_jobs(session, *page, document_id=document_id, status=status)
    return ok(request, [job_out(session, j) for j in rows], nxt)


@jobs.get("/jobs/{job_id}", response_model=M.Envelope[M.JobOut])
def get_job(request: Request, job_id: str, session: Session = Depends(get_session)):
    return ok(request, job_out(session, jobs_svc.get_job(session, job_id)))


@jobs.get("/jobs/{job_id}/results", response_model=M.Envelope[M.ResultsOut])
def get_results(
    request: Request, job_id: str, version: Optional[int] = Query(None, ge=1), session: Session = Depends(get_session)
):
    return ok(request, get_result_version(session, jobs_svc.get_job(session, job_id), version))


@jobs.get("/jobs/{job_id}/validation", response_model=M.Envelope[list[M.ValidationOut]])
def get_validation(
    request: Request,
    job_id: str,
    version: Optional[int] = Query(None, ge=1),
    status: Optional[str] = Query(None, description="pass | fail"),
    session: Session = Depends(get_session),
):
    job = jobs_svc.get_job(session, job_id)
    keys = {
        r.id: r.record_key for r in session.scalars(select(ExtractedRecord).where(ExtractedRecord.job_id == job.id))
    }
    rows = session.scalars(
        select(ValidationResult).where(ValidationResult.job_id == job.id).order_by(ValidationResult.result_version)
    )
    latest: dict[tuple[str, str], ValidationResult] = {}
    for v in rows:  # latest outcome per (record, rule) at or before `version`
        if version is None or v.result_version <= version:
            latest[(v.record_id, v.rule_id)] = v
    out = []
    for v in latest.values():
        if v.status == "n/a":
            continue
        wire_status = "pass" if v.status == "pass" else "fail"
        if status is not None and wire_status != status:
            continue
        out.append(
            {
                "id": v.id,
                "record_id": v.record_id,
                "record_key": keys.get(v.record_id),
                "field_names": v.field_names or [],
                "rule_id": v.rule_id,
                "rule_version": v.rule_version,
                "severity": SEVERITY_WIRE.get(v.severity, "info"),
                "status": wire_status,
                "expected_value": v.expected_value,
                "actual_value": v.actual_value,
                "message": v.message,
                "result_version": v.result_version,
            }
        )
    return ok(request, out)


@jobs.get("/jobs/{job_id}/trace", response_model=M.Envelope[list[M.TraceEventOut]])
def get_trace(request: Request, job_id: str, session: Session = Depends(get_session)):
    job = jobs_svc.get_job(session, job_id)
    actions = session.scalars(select(AgentAction).where(AgentAction.job_id == job.id).order_by(AgentAction.seq))
    return ok(request, [trace_out(a) for a in actions])


@jobs.post("/jobs/{job_id}/retry", status_code=202, response_model=M.Envelope[M.JobOut])
def retry_job(request: Request, job_id: str, session: Session = Depends(get_session)):
    """Creates a new run for the same document; the response is the new job."""
    job = jobs_svc.retry_job(session, job_id)
    session.commit()
    get_queue(get_settings()).publish(job.id)
    return ok(request, job_out(session, job), status_code=202)


@jobs.post("/jobs/{job_id}/cancel", status_code=202, response_model=M.Envelope[M.JobOut])
def cancel_job(request: Request, job_id: str, session: Session = Depends(get_session)):
    return ok(request, job_out(session, jobs_svc.cancel_job(session, job_id)), status_code=202)


# --- evidence & artifacts
@evidence.get("/evidence/{evidence_id}", response_model=M.Envelope[M.EvidenceOut])
def get_evidence(request: Request, evidence_id: str, session: Session = Depends(get_session)):
    ev = session.get(Evidence, evidence_id)
    if ev is None or not ev.bounding_box:
        raise not_found("evidence", evidence_id)
    return ok(request, serialize_evidence(ev))


@evidence.get("/fields/{field_id}/evidence", response_model=M.Envelope[list[M.EvidenceOut]])
def get_field_evidence(request: Request, field_id: str, session: Session = Depends(get_session)):
    return ok(request, serialize_field(review_svc.get_field(session, field_id))["evidence"])


@evidence.get("/artifacts/{artifact_id}", responses={200: {"content": {"image/png": {}}}})
def get_artifact(artifact_id: str, session: Session = Depends(get_session)):
    art = session.get(Artifact, artifact_id)
    if art is None:
        raise not_found("artifact", artifact_id)
    return Response(
        get_storage(get_settings()).get(art.storage_key),
        media_type=art.content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --- review
@review.get("/jobs/{job_id}/review-queue", response_model=M.Envelope[list[M.ReviewQueueItem]])
def get_review_queue(request: Request, job_id: str, session: Session = Depends(get_session)):
    jobs_svc.get_job(session, job_id)
    return ok(request, review_svc.review_queue(session, job_id))


@review.get("/fields/{field_id}", response_model=M.Envelope[M.FieldOut])
def get_field(request: Request, field_id: str, session: Session = Depends(get_session)):
    return ok(request, serialize_field(review_svc.get_field(session, field_id)))


@review.post("/fields/{field_id}/review", response_model=M.Envelope[M.ResultsOut])
def post_review(request: Request, field_id: str, body: M.ReviewIn, session: Session = Depends(get_session)):
    """Applies the decision and returns the job's complete new result version."""
    out = review_svc.submit_review(
        session,
        get_settings(),
        field_id,
        body.decision,
        body.corrected_value,
        body.reason_code,
        body.comment,
        body.evidence_ids,
        body.reviewer_name,
        body.expected_result_version,
    )
    return ok(request, out)


@review.get("/fields/{field_id}/history", response_model=M.Envelope[list[M.ReviewHistoryOut]])
def get_history(request: Request, field_id: str, session: Session = Depends(get_session)):
    return ok(request, review_svc.field_history(session, field_id))


# --- exports
@exports.post("/jobs/{job_id}/exports", status_code=201, response_model=M.Envelope[M.ExportItem])
def create_export(request: Request, job_id: str, body: M.ExportIn, session: Session = Depends(get_session)):
    exp = export_svc.create_export(
        session, get_settings(), job_id, body.format, body.result_version, body.include_evidence_manifest
    )
    session.refresh(exp)
    return ok(request, export_out(exp), status_code=201)


@exports.get("/jobs/{job_id}/exports", response_model=M.Envelope[list[M.ExportItem]])
def list_exports(request: Request, job_id: str, session: Session = Depends(get_session)):
    jobs_svc.get_job(session, job_id)
    rows = session.scalars(
        select(ExportArtifact).where(ExportArtifact.job_id == job_id).order_by(ExportArtifact.created_at.desc())
    )
    return ok(request, [export_out(e) for e in rows])


@exports.get("/exports/{export_id}", response_model=M.Envelope[M.ExportItem])
def get_export(request: Request, export_id: str, session: Session = Depends(get_session)):
    return ok(request, export_out(export_svc.get_export(session, export_id)))


@exports.get("/exports/{export_id}/download", responses={200: {"content": {"text/csv": {}, "application/json": {}}}})
def download_export(
    export_id: str, part: str = Query("data", pattern="^(data|manifest)$"), session: Session = Depends(get_session)
):
    body, media, filename = export_svc.export_payload(session, get_settings(), export_id, part)
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --- evaluations
@evaluations.post("/evaluations", status_code=202, response_model=M.Envelope[M.EvaluationReportOut])
def create_evaluation(request: Request, body: Optional[M.EvaluationIn] = None, session: Session = Depends(get_session)):
    body = body or M.EvaluationIn()
    settings = get_settings()
    run = eval_svc.create_evaluation(
        session, settings, body.configurations or body.configs, body.variants, body.limit, body.dataset_version
    )
    session.commit()
    eval_svc.start_evaluation(settings, run.id)
    return ok(request, eval_svc.report_out(run), status_code=202)


@evaluations.get("/evaluations", response_model=M.Envelope[list[M.EvaluationReportOut]])
def list_evaluations(request: Request, page=Depends(page_params), session: Session = Depends(get_session)):
    rows, nxt = eval_svc.list_evaluations(session, *page)
    return ok(request, [eval_svc.report_out(r) for r in rows], nxt)


@evaluations.get("/evaluations/{evaluation_id}", response_model=M.Envelope[M.EvaluationReportOut])
def get_evaluation(request: Request, evaluation_id: str, session: Session = Depends(get_session)):
    return ok(request, eval_svc.report_out(eval_svc.get_evaluation(session, evaluation_id)))


@evaluations.get(
    "/evaluations/{evaluation_id}/report",
    response_model=M.Envelope[M.EvaluationReportOut],
    responses={200: {"content": {"application/json": {}, "text/markdown": {}}}},
)
def get_report(
    request: Request,
    evaluation_id: str,
    format: str = Query("json", pattern="^(json|md)$"),
    session: Session = Depends(get_session),
):
    """EvaluationReport including status while in progress; ?format=md returns the markdown report."""
    if format == "md":
        return Response(
            eval_svc.evaluation_markdown(session, get_settings(), evaluation_id),
            media_type="text/markdown; charset=utf-8",
        )
    return ok(request, eval_svc.report_out(eval_svc.get_evaluation(session, evaluation_id)))


# ------------------------------------------------------------------ rate limiting
class RateLimiter:
    """Limit on mutating requests per client IP.

    With Redis the counter is shared by every API replica (fixed one-minute window);
    without it, or if Redis is unreachable, an in-process sliding window is used.
    """

    def __init__(self, per_minute: int, redis_client=None):
        self.per_minute = per_minute
        self.redis = redis_client
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        if self.redis is not None:
            try:
                bucket = f"beeproof:rl:{key}:{int(time.time() // 60)}"
                pipe = self.redis.pipeline()
                pipe.incr(bucket)
                pipe.expire(bucket, 60)
                count, _ = pipe.execute()
                return int(count) <= self.per_minute
            except Exception as exc:
                log.warning("redis rate limiter unavailable, using in-process limits: %s", exc)
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


# ------------------------------------------------------------------ app
def create_app(start_worker: bool | None = None) -> FastAPI:
    settings = get_settings()
    logging_setup.configure(settings.log_format)
    queue = get_queue(settings)
    limiter = RateLimiter(settings.rate_limit_per_min, queue.client if isinstance(queue, RedisQueue) else None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(settings)
        with session_scope() as s:
            schema_svc.ensure_builtin_schema(s)
        worker = None
        run_worker = start_worker if start_worker is not None else settings.worker_mode == "inline"
        if run_worker:
            from beeproof.worker import Worker

            worker = Worker(settings)
            worker.start()
        yield
        if worker:
            worker.stop()

    app = FastAPI(
        title="BeeProof AI API",
        version=PIPELINE_VERSION,
        lifespan=lifespan,
        description="Evidence-first extraction of bank rate sheets. Open access: no authentication "
        "(ADR-004). JSON responses use the {data, meta} envelope; errors use {error}. "
        "Wire types match the frontend's lib/types.ts.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    def err(request: Request, status: int, code: str, message: str, details=None):
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "details": details or [], "request_id": rid(request)}},
        )

    @app.middleware("http")
    async def request_id_and_limits(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        logging_setup.request_id_var.set(request.state.request_id)
        logging_setup.trace_var.set(
            logging_setup.trace_from_header(request.headers.get("X-Cloud-Trace-Context"), settings.project)
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                response = err(request, 429, "RATE_LIMITED", "Too many requests; slow down and retry shortly.")
                response.headers["Retry-After"] = "60"
                response.headers["X-Request-ID"] = request.state.request_id
                return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return err(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e.get("loc", []) if p != "body"), "reason": e.get("msg", "")}
            for e in exc.errors()
        ]
        return err(request, 422, "VALIDATION_ERROR", "The request did not satisfy the required schema.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return err(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error (request %s)", rid(request))
        return err(request, 500, "INTERNAL_ERROR", "An unexpected error occurred.")

    for r in (health, documents, schemas, jobs, evidence, review, exports, evaluations):
        app.include_router(r)
    return app


app = create_app()
