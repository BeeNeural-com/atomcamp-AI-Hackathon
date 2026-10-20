"""Job lifecycle: creation, authoritative state transitions, atomic claim, retry, cancel, recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from beeproof.config import PIPELINE_VERSION, Settings
from beeproof.domain.states import PROCESSING, TERMINAL, InvalidTransition, JobStatus, check_transition
from beeproof.domain.validators import RULES_VERSION
from beeproof.orm import AgentAction, ExtractionJob, utcnow
from beeproof.prompts import PROMPT_VERSION
from beeproof.services.documents import get_document
from beeproof.services.errors import AppError, not_found
from beeproof.services.schemas import resolve_version


def configuration_snapshot(settings: Settings, options: dict) -> dict:
    return {
        "extractor": settings.extractor,
        "gemini_model": settings.gemini_model or None,
        "gemini_location": settings.gemini_location if settings.extractor == "gemini" else None,
        "layout_provider": settings.layout_provider,
        "docai_processor_configured": bool(settings.docai_processor_id),
        "tau": settings.tau,
        "max_repair_attempts": settings.max_relook_loops,
        "max_repair_calls_per_job": settings.max_relook_calls_per_job,
        "rules_version": RULES_VERSION,
        "prompt_version": PROMPT_VERSION,
        "flags": {
            "witness_a": bool(options.get("witness_a", settings.flags.witness_a)),
            "validators": True,
            "relook": bool(options.get("enable_repair", settings.flags.relook)),
            "memory": bool(options.get("use_memory", settings.flags.memory)),
        },
        "require_evidence": bool(options.get("require_evidence", True)),
    }


def settings_for_job(settings: Settings, job: ExtractionJob) -> Settings:
    flags = (job.configuration_snapshot or {}).get("flags", {})
    allowed = {"witness_a", "validators", "relook", "memory"}
    return settings.with_flags(**{k: v for k, v in flags.items() if k in allowed})


def create_job(
    session: Session,
    settings: Settings,
    document_id: str,
    schema_version_id: str | None,
    options: dict,
    idempotency_key: str | None,
) -> tuple[ExtractionJob, bool]:
    """Returns (job, created). Re-sending the same idempotency key returns the original job."""
    doc = get_document(session, document_id)
    if idempotency_key:
        existing = session.scalar(select(ExtractionJob).where(ExtractionJob.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.document_id != doc.id:
                raise AppError("IDEMPOTENCY_CONFLICT", "idempotency_key was already used for another document", 409)
            return existing, False
    version = resolve_version(session, schema_version_id)
    job = ExtractionJob(
        document_id=doc.id,
        schema_version_id=version.id,
        status=JobStatus.QUEUED.value,
        stage=JobStatus.QUEUED.value,
        max_attempts=settings.max_job_attempts,
        configuration_snapshot=configuration_snapshot(settings, options or {}),
        pipeline_version=PIPELINE_VERSION,
        idempotency_key=idempotency_key,
    )
    session.add(job)
    session.flush()
    return job, True


def get_job(session: Session, job_id: str) -> ExtractionJob:
    job = session.get(ExtractionJob, job_id)
    if job is None:
        raise not_found("job", job_id)
    return job


def list_jobs(
    session: Session, limit: int, offset: int, document_id: str | None = None, status: str | None = None
) -> tuple[list[ExtractionJob], int | None]:
    q = select(ExtractionJob)
    if document_id:
        q = q.where(ExtractionJob.document_id == document_id)
    if status:
        q = q.where(ExtractionJob.status == status)
    rows = list(
        session.scalars(q.order_by(ExtractionJob.created_at.desc(), ExtractionJob.id).offset(offset).limit(limit + 1))
    )
    return rows[:limit], (offset + limit if len(rows) > limit else None)


def transition(job: ExtractionJob, target: JobStatus | str) -> None:
    """The single authoritative way to change job status."""
    target = JobStatus(target)
    if JobStatus(job.status) == target:
        return
    try:
        check_transition(job.status, target)
    except InvalidTransition as exc:
        raise AppError("JOB_STATE_CONFLICT", str(exc), 409) from exc
    job.status = target.value
    job.stage = target.value
    now = utcnow()
    if target in PROCESSING:
        job.heartbeat_at = now
    if target in TERMINAL:
        job.completed_at = now


def next_seq(session: Session, job_id: str) -> int:
    return (session.scalar(select(func.max(AgentAction.seq)).where(AgentAction.job_id == job_id)) or 0) + 1


def log_action(
    session: Session,
    job: ExtractionJob,
    stage: str,
    agent: str,
    action: str,
    reason: str,
    summary: str,
    status: str = "succeeded",
    **extra,
) -> AgentAction:
    a = AgentAction(
        job_id=job.id,
        seq=next_seq(session, job.id),
        trace_id=job.trace_id,
        stage=stage,
        agent_name=agent,
        action_type=action,
        reason_code=reason,
        summary=summary,
        result_status=status,
        **extra,
    )
    session.add(a)
    session.flush()
    return a


def claim_next(session: Session, worker_id: str) -> str | None:
    """Atomically move one queued job to preprocessing; returns its id."""
    candidates = session.scalars(
        select(ExtractionJob.id)
        .where(ExtractionJob.status == JobStatus.QUEUED.value)
        .order_by(ExtractionJob.created_at)
        .limit(5)
    ).all()
    now = utcnow()
    for job_id in candidates:
        res = session.execute(
            update(ExtractionJob)
            .where(ExtractionJob.id == job_id, ExtractionJob.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.PREPROCESSING.value,
                stage=JobStatus.PREPROCESSING.value,
                claimed_by=worker_id,
                heartbeat_at=now,
                started_at=now,
                attempt_count=ExtractionJob.attempt_count + 1,
            )
        )
        if res.rowcount == 1:
            session.commit()
            return job_id
    session.commit()
    return None


def claim_job(session: Session, job_id: str, worker_id: str) -> bool:
    """Atomically claim one specific queued job (push delivery). False if it is not claimable."""
    now = utcnow()
    res = session.execute(
        update(ExtractionJob)
        .where(ExtractionJob.id == job_id, ExtractionJob.status == JobStatus.QUEUED.value)
        .values(
            status=JobStatus.PREPROCESSING.value,
            stage=JobStatus.PREPROCESSING.value,
            claimed_by=worker_id,
            heartbeat_at=now,
            started_at=now,
            attempt_count=ExtractionJob.attempt_count + 1,
        )
    )
    session.commit()
    return res.rowcount == 1


def queued_job_ids(session: Session, older_than_seconds: int, limit: int = 100) -> list[str]:
    """Queued jobs that have waited long enough that their delivery may have been lost."""
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    rows = session.scalars(
        select(ExtractionJob)
        .where(ExtractionJob.status == JobStatus.QUEUED.value)
        .order_by(ExtractionJob.created_at)
        .limit(limit)
    )
    out = []
    for job in rows:
        ts = job.heartbeat_at or job.created_at
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts is None or ts < cutoff:
            out.append(job.id)
    return out


def retry_job(session: Session, job_id: str) -> ExtractionJob:
    """Explicit retry creates a new run with the same document, schema and configuration.

    The failed/cancelled job stays terminal and keeps its audit trail.
    """
    old = get_job(session, job_id)
    if old.status not in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
        raise AppError(
            "JOB_STATE_CONFLICT", f"only failed or cancelled jobs can be retried (status is {old.status})", 409
        )
    new = ExtractionJob(
        document_id=old.document_id,
        schema_version_id=old.schema_version_id,
        status=JobStatus.QUEUED.value,
        stage=JobStatus.QUEUED.value,
        retry_of=old.id,
        max_attempts=old.max_attempts,
        configuration_snapshot=dict(old.configuration_snapshot or {}),
        pipeline_version=PIPELINE_VERSION,
    )
    session.add(new)
    session.flush()
    log_action(
        session,
        old,
        old.status,
        "supervisor",
        "retry",
        "MANUAL_RETRY",
        f"superseded by new run {new.id}",
        output_refs=[f"job:{new.id}"],
    )
    log_action(
        session,
        new,
        "queued",
        "supervisor",
        "retry",
        "MANUAL_RETRY",
        f"new run created from job {old.id}",
        input_refs=[f"job:{old.id}"],
    )
    return new


def cancel_job(session: Session, job_id: str) -> ExtractionJob:
    job = get_job(session, job_id)
    status = JobStatus(job.status)
    if status in TERMINAL:
        raise AppError("JOB_STATE_CONFLICT", f"job is already {job.status}", 409)
    if status in PROCESSING:
        job.cancel_requested = True  # worker stops at the next stage boundary
        log_action(
            session,
            job,
            job.stage,
            "supervisor",
            "cancel",
            "CANCEL_REQUESTED",
            "cancellation requested; worker will stop at the next stage boundary",
        )
    else:
        transition(job, JobStatus.CANCELLED)
        log_action(session, job, "cancelled", "supervisor", "cancel", "CANCELLED", "job cancelled")
    return job


def recover_stale(session: Session, stale_seconds: int) -> int:
    """Return jobs whose worker stopped heart-beating to a safe state."""
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    n = 0
    for job in session.scalars(select(ExtractionJob).where(ExtractionJob.status.in_([s.value for s in PROCESSING]))):
        hb = job.heartbeat_at
        if hb is not None and hb.tzinfo is None:
            hb = hb.replace(tzinfo=UTC)
        if hb is not None and hb > cutoff:
            continue
        if job.attempt_count < job.max_attempts:
            transition(job, JobStatus.QUEUED)
            reason, summary = "STALE_REQUEUED", "worker heartbeat lost; job re-queued"
        else:
            transition(job, JobStatus.FAILED)
            job.error_code, job.error_message = "WORKER_LOST", "worker heartbeat lost and attempts exhausted"
            reason, summary = "STALE_FAILED", job.error_message
        job.claimed_by = None
        log_action(session, job, job.status, "supervisor", "recover", reason, summary)
        n += 1
    return n
