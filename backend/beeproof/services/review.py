"""Human review (PRODUCT_SPEC.md §6.3, CLAUDE_BACKEND_HANDOFF.md "Review").

* Optimistic concurrency: `expected_result_version` must equal the job's current version (409).
* A human decision never bypasses a hard rule: accepting a field that fails one, or correcting
  it to a value that still fails one, is refused with 422 MANDATORY_VALIDATION_FAILED.
* Every decision appends a complete new result snapshot and re-runs all dependent rules.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from beeproof.config import Settings
from beeproof.contracts import FieldEvidence, RuleResult
from beeproof.domain.normalizers import normalize_field
from beeproof.domain.scoring import field_confidence
from beeproof.domain.states import JobStatus
from beeproof.domain.validators import apply_rules, validate_cross_records, validate_record
from beeproof.orm import Evidence, ExtractedField, ExtractedRecord, ExtractionJob, ReviewDecision, utcnow
from beeproof.services.errors import AppError, not_found
from beeproof.services.jobs import log_action, transition
from beeproof.services.results import (
    _validation_row,
    commit_result_version,
    encode_value,
    serialize_field,
    serialize_results,
    to_draft,
    wire_validations,
)

DECISIONS = {"accept", "correct", "reject", "defer"}
OPEN = {"pending", "in_review", "deferred"}
RESOLVED = {"accepted", "corrected", "not_required"}
REVIEWABLE_JOB_STATES = {JobStatus.REVIEW_REQUIRED.value, JobStatus.COMPLETED.value}


def get_field(session: Session, field_id: str) -> ExtractedField:
    f = session.get(ExtractedField, field_id)
    if f is None:
        raise not_found("field", field_id)
    return f


def review_queue(session: Session, job_id: str) -> list[dict]:
    """Open review items, lowest confidence first."""
    rows = session.execute(
        select(ExtractedField, ExtractedRecord)
        .join(ExtractedRecord, ExtractedField.record_id == ExtractedRecord.id)
        .where(ExtractedRecord.job_id == job_id, ExtractedField.review_status.in_(OPEN))
        .order_by(ExtractedField.confidence_score, ExtractedRecord.record_index)
    ).all()
    items = []
    for f, rec in rows:
        failing = {code for code, st in (f.validators or {}).items() if st in {"fail", "warn"}}
        reasons = [r for r in (rec.review_reasons or []) if r.split(":", 1)[0] in failing]
        items.append(
            {
                "field": serialize_field(f),
                "record_id": rec.id,
                "record_index": rec.record_index,
                "record_key": rec.record_key,
                "product_label": rec.product_label,
                "page_number": rec.page_number,
                "decision": rec.decision,
                "record_values": rec.data_json,
                "reasons": reasons or rec.review_reasons or [],
            }
        )
    return items


def _hard_failures_for(rules: list[RuleResult], field_name: str) -> list[RuleResult]:
    return [r for r in rules if r.severity == "HARD" and r.status == "fail" and field_name in r.fields]


def _refuse(failures: list[RuleResult], what: str) -> AppError:
    return AppError(
        "MANDATORY_VALIDATION_FAILED",
        f"{what}: {failures[0].message}",
        422,
        [{"field": ",".join(r.fields), "reason": f"{r.rule_id}: {r.message}"} for r in failures],
    )


def _check_decision(rec: ExtractedRecord, f: ExtractedField, decision: str, corrected_value: str | None) -> str | None:
    """Server-side gate. Returns the encoded corrected value for 'correct'."""
    draft = to_draft(rec)
    if decision == "correct":
        if corrected_value is None or str(corrected_value).strip() == "":
            raise AppError("REVIEW_DECISION_INVALID", "corrected_value is required for decision 'correct'", 422)
        typed = normalize_field(f.field_name, corrected_value)
        if typed is None:
            raise AppError(
                "REVIEW_DECISION_INVALID", f"'{corrected_value}' is not a valid {f.data_type} for {f.field_name}", 422
            )
        draft.fields[f.field_name].value = typed
        failures = _hard_failures_for(validate_record(draft), f.field_name)
        if failures:
            raise _refuse(failures, "The corrected value fails validation")
        return encode_value(f.field_name, typed, str(corrected_value))
    if decision == "accept":
        failures = _hard_failures_for(validate_record(draft), f.field_name)
        if failures:
            raise _refuse(failures, "This value fails a blocking rule and cannot be accepted; correct or reject it")
    return None


def _apply_decision(
    f: ExtractedField, decision: str, new_value: str | None, reviewer_name: str | None, reason_code: str | None
) -> None:
    history = list(f.transformation_history_json or [])
    history.append(
        {
            "step": f"human_{decision}",
            "from": f.normalized_value,
            "to": new_value if decision == "correct" else f.normalized_value,
            "reviewer_name": reviewer_name,
            "reason_code": reason_code,
            "at": utcnow().isoformat(),
        }
    )
    f.transformation_history_json = history
    if decision == "correct":
        f.normalized_value = new_value
        f.review_status, f.verification_status = "corrected", "human_verified"
        f.confidence_score = 1.0
        f.version += 1
    elif decision == "accept":
        f.review_status, f.verification_status = "accepted", "human_verified"
        f.confidence_score = 1.0
    elif decision == "reject":
        f.review_status, f.verification_status = "rejected", "rejected"
    else:
        f.review_status = "deferred"


def _refresh_record(r: ExtractedRecord, rules: list[RuleResult], tau: float, witness_a_enabled: bool) -> bool:
    """Recompute review state of a record after revalidation. Returns True if it changed."""
    fields = {x.field_name: x for x in r.fields}
    before = (r.status, r.needs_review, dict(r.data_json or {}), tuple(r.reason_codes or []))
    failing_by_field: dict[str, list[RuleResult]] = {}
    for rr in rules:
        if rr.failed:
            for n in rr.fields:
                failing_by_field.setdefault(n, []).append(rr)
    for name, ef in fields.items():
        ef.validators = {rr.rule_id: rr.status for rr in rules if name in rr.fields}
        if ef.verification_status != "human_verified" and ef.normalized_value is not None:
            # confidence follows the current rule outcomes (ARCHITECTURE.md §8)
            ef.confidence_score = field_confidence(
                FieldEvidence(
                    name=name,
                    value=ef.normalized_value,
                    agreement=ef.agreement,
                    grounded=ef.grounded,
                    witness_a_conf=ef.witness_a_conf,
                    validators=ef.validators,
                ),
                witness_a_enabled,
                True,
            )
        hard_here = any(rr.severity == "HARD" for rr in failing_by_field.get(name, []))
        if ef.review_status == "pending" and name not in failing_by_field and ef.confidence_score >= tau:
            ef.review_status = "not_required"  # its reason went away
        elif ef.review_status in {"not_required", "accepted", "corrected"} and hard_here:
            ef.review_status = "pending"  # a dependent hard rule now fails: approval invalidated
    statuses = {x.review_status for x in fields.values()}
    hard_open = [rr for rr in rules if rr.severity == "HARD" and rr.status == "fail"]
    if "rejected" in statuses:
        r.status, r.needs_review = "rejected", True
    elif statuses & OPEN or hard_open:
        r.status, r.needs_review = r.decision.lower(), True
    elif r.needs_review or r.status == "human_verified":
        r.status, r.needs_review = "human_verified", False
    r.data_json = {n: x.normalized_value for n, x in fields.items()}
    r.validations_json = wire_validations(rules)
    present = [x.confidence_score for x in fields.values() if x.normalized_value is not None]
    r.extraction_confidence = round(min(present), 2) if present else 0.0
    if r.status != "auto_approved":
        failed = [rr for rr in rules if rr.failed]
        r.review_reasons = [f"{rr.rule_id}: {rr.message}" for rr in failed]
        r.reason_codes = [rr.rule_id for rr in failed]
    changed = before != (r.status, r.needs_review, r.data_json, tuple(r.reason_codes or []))
    if changed:
        r.version += 1
    return changed


def submit_review(
    session: Session,
    settings: Settings,
    field_id: str,
    decision: str,
    corrected_value: str | None,
    reason_code: str | None,
    comment: str | None,
    evidence_ids: list[str],
    reviewer_name: str | None,
    expected_result_version: int | None = None,
) -> dict:
    if decision not in DECISIONS:
        raise AppError("REVIEW_DECISION_INVALID", f"decision must be one of {sorted(DECISIONS)}", 422)
    f = get_field(session, field_id)
    rec: ExtractedRecord = f.record
    job: ExtractionJob = rec.job
    if job.status not in REVIEWABLE_JOB_STATES:
        raise AppError("JOB_STATE_CONFLICT", f"job is {job.status}; review is only possible after processing", 409)
    if expected_result_version is not None and expected_result_version != job.result_version:
        raise AppError(
            "STALE_RESULT_VERSION",
            f"results changed (current version {job.result_version}, you reviewed "
            f"{expected_result_version}); reload and review again",
            409,
        )
    for ev_id in evidence_ids or []:
        ev = session.get(Evidence, ev_id)
        if ev is None or ev.document_id != job.document_id:
            raise AppError(
                "EVIDENCE_NOT_FOUND",
                f"evidence {ev_id} does not belong to this document",
                422,
                [{"field": "evidence_ids", "reason": f"unknown evidence {ev_id}"}],
            )

    previous = f.normalized_value
    new_value = _check_decision(rec, f, decision, corrected_value)
    _apply_decision(f, decision, new_value, reviewer_name, reason_code)
    session.flush()

    version = job.result_version + 1
    records = list(
        session.scalars(
            select(ExtractedRecord).where(ExtractedRecord.job_id == job.id).order_by(ExtractedRecord.record_index)
        )
    )
    drafts = {r.id: to_draft(r) for r in records}
    cross = validate_cross_records(list(drafts.values()))
    witness_a_enabled = (job.configuration_snapshot or {}).get("flags", {}).get("witness_a", True)
    for r in records:
        d = drafts[r.id]
        rules = validate_record(d) + cross.get(d.key, [])
        apply_rules(d, rules)
        r.inferred_compounding = d.inferred_compounding
        if _refresh_record(r, rules, settings.tau, witness_a_enabled) or r.id == rec.id:
            for rr in rules:
                session.add(_validation_row(job.id, r.id, rr, version))

    session.add(
        ReviewDecision(
            job_id=job.id,
            field_id=f.id,
            reviewer_name=reviewer_name or None,
            previous_value=previous,
            corrected_value=new_value,
            decision=decision,
            reason_code=reason_code,
            comment=comment,
            evidence_ids_json=evidence_ids or [],
            institution=(rec.data_json or {}).get("institution_name"),
            expected_result_version=expected_result_version,
            result_version=version,
        )
    )
    log_action(
        session,
        job,
        "review",
        "human_reviewer",
        "review_decision",
        reason_code or decision.upper(),
        f"{rec.record_key}.{f.field_name}: {decision}" + (f" {previous} -> {new_value}" if new_value else ""),
        input_refs=[f"field:{f.id}"] + [f"evidence:{e}" for e in evidence_ids or []],
    )
    session.flush()

    still_open = session.scalar(
        select(ExtractedField.id)
        .join(ExtractedRecord, ExtractedField.record_id == ExtractedRecord.id)
        .where(ExtractedRecord.job_id == job.id, ExtractedField.review_status.in_(OPEN))
        .limit(1)
    )
    still_flagged = session.scalar(
        select(ExtractedRecord.id)
        .where(
            ExtractedRecord.job_id == job.id,
            ExtractedRecord.needs_review.is_(True),
            ExtractedRecord.status != "rejected",
        )
        .limit(1)
    )
    if job.status == JobStatus.REVIEW_REQUIRED.value and still_open is None and still_flagged is None:
        transition(job, JobStatus.VALIDATING)
        transition(job, JobStatus.COMPLETED)
        log_action(
            session,
            job,
            "completed",
            "review_router",
            "complete",
            "REVIEW_COMPLETE",
            "every review item has a decision; job completed (rejected records stay flagged)",
        )
    elif job.status == JobStatus.COMPLETED.value and still_flagged is not None:
        # a late correction re-opened a record; completed is terminal, so the record stays flagged in results
        log_action(
            session,
            job,
            "review",
            "review_router",
            "reopen",
            "REVIEW_REOPENED",
            "a dependent rule failed after a correction; affected records are flagged again",
            status="warning",
        )
    commit_result_version(session, job, f"review:{decision}")
    return serialize_results(session, job)


def field_history(session: Session, field_id: str) -> list[dict]:
    """ReviewHistory[] (lib/types.ts), chronological."""
    get_field(session, field_id)
    decisions = session.scalars(
        select(ReviewDecision).where(ReviewDecision.field_id == field_id).order_by(ReviewDecision.created_at)
    )
    return [
        {
            "id": d.id,
            "field_id": d.field_id,
            "decision": d.decision,
            "corrected_value": d.corrected_value,
            "previous_value": d.previous_value,
            "reason_code": d.reason_code or "",
            "comment": d.comment or "",
            "reviewer_name": d.reviewer_name or "",
            "evidence_ids": d.evidence_ids_json or [],
            "expected_result_version": d.expected_result_version,
            "result_version": d.result_version,
            "created_at": d.created_at.isoformat(),
        }
        for d in decisions
    ]
