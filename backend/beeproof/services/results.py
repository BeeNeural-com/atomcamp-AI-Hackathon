"""Persist pipeline output and serialize versioned results.

Wire shapes follow the frontend contract in `lib/types.ts` (RateRecord, ExtractedField,
Evidence, Validation); extra provenance keys are additive.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beeproof.config import Settings
from beeproof.contracts import FIELD_TYPES, FIELD_UNITS, FIELDS, Decision, FieldEvidence, RecordDraft, RuleResult
from beeproof.domain.normalizers import decimals_in
from beeproof.domain.scoring import fields_needing_review
from beeproof.domain.states import JobStatus
from beeproof.orm import (
    AgentAction,
    Artifact,
    Evidence,
    ExtractedField,
    ExtractedRecord,
    ExtractionJob,
    FieldEvidenceLink,
    ResultVersion,
    ValidationResult,
    new_id,
)
from beeproof.pipeline.geometry import to_xywh
from beeproof.pipeline.orchestrator import PipelineResult
from beeproof.providers.storage import get_storage
from beeproof.services.errors import AppError
from beeproof.services.jobs import next_seq, transition

# wire enums (lib/types.ts)
RECORD_STATUS_WIRE = {
    "auto_approved": "validated",
    "needs_review": "needs_review",
    "document_inconsistency": "needs_review",
    "human_verified": "human_verified",
    "rejected": "rejected",
}
REVIEW_STATUS_WIRE = {
    "not_required": "accepted",
    "pending": "pending",
    "in_review": "pending",
    "accepted": "accepted",
    "corrected": "corrected",
    "rejected": "rejected",
    "deferred": "deferred",
}
SEVERITY_WIRE = {"HARD": "error", "SOFT": "warning"}


# ------------------------------------------------------------------ value encoding
def encode_value(name: str, value, raw: str | None = None) -> str | None:
    """Decimal-as-string encoding used in JSON and the database."""
    if value is None:
        return None
    if name in {"rate_pct", "apy_pct"}:
        d = decimals_in(raw) if raw else None
        if d is None:
            return format(Decimal(str(value)).normalize(), "f")
        return f"{float(value):.{d}f}"
    if name == "min_deposit":
        return f"{float(value):.2f}"
    if name == "term_months":
        return str(int(value))
    return str(value)


def decode_value(name: str, text: str | None):
    if text is None:
        return None
    if name in {"rate_pct", "apy_pct", "min_deposit"}:
        return float(text)
    if name == "term_months":
        return int(text)
    return text


def wire_validations(rules: list[RuleResult]) -> list[dict]:
    """Rule outcomes in the frontend shape; not-applicable rules are omitted."""
    return [
        {
            "rule_id": r.rule_id,
            "severity": SEVERITY_WIRE[r.severity],
            "status": "pass" if r.status == "pass" else "fail",
            "message": r.message,
            "field_names": [n for n in r.fields if n in FIELDS],
            "rule_version": r.rule_version,
            "expected_value": r.expected,
            "actual_value": r.actual,
        }
        for r in rules
        if r.status != "n/a"
    ]


def record_status(decision: str, needs_review: bool) -> str:
    if not needs_review:
        return "auto_approved" if decision == Decision.AUTO_APPROVED.value else "human_verified"
    return decision.lower()


def verification_status(f: FieldEvidence, rules_failed: bool) -> str:
    if f.unresolved:
        return "unresolved"
    if f.value is None:
        return "pending"
    if f.bbox or f.source_type in {"filename", "derived"}:
        return "evidence_linked" if rules_failed else "validated"
    return "pending"


# ------------------------------------------------------------------ persistence
def persist_result(session: Session, settings: Settings, job: ExtractionJob, result: PipelineResult) -> None:
    if session.scalar(select(ExtractedRecord.id).where(ExtractedRecord.job_id == job.id).limit(1)):
        raise AppError("JOB_STATE_CONFLICT", "results already persisted for this job", 409)
    storage = get_storage(settings)
    require_evidence = (job.configuration_snapshot or {}).get("require_evidence", True)
    page_kind = {p.page_no: p.kind for p in result.pages}

    for page in job.document.pages:
        reading = result.readings.get(page.page_number)
        if reading is not None:
            page.layout_provider = reading.provider

    if require_evidence:
        for rec in result.records:
            missing = [
                f.name
                for f in rec.fields.values()
                if f.value is not None and f.source_type in {"table_cell", "page_header"} and not f.bbox
            ]
            if missing and rec.decision == Decision.AUTO_APPROVED:
                rec.decision = Decision.NEEDS_REVIEW
                rec.reason_codes.append("EVIDENCE_MISSING")
                rec.review_reasons.append(f"EVIDENCE_MISSING: no source region for {', '.join(missing)}")

    field_ids: dict[str, str] = {}
    for idx, rec in enumerate(result.records):
        review_fields = fields_needing_review(rec, settings.tau)
        db_rec = ExtractedRecord(
            id=new_id(),
            job_id=job.id,
            record_index=idx,
            record_key=rec.key,
            source_file=rec.source_file,
            page_number=rec.page,
            product_label=rec.product_label,
            row_bbox=rec.row_bbox,
            decision=rec.decision.value,
            status=record_status(rec.decision.value, rec.needs_review),
            extraction_confidence=rec.extraction_confidence,
            needs_review=rec.needs_review,
            review_reasons=rec.review_reasons,
            reason_codes=rec.reason_codes,
            inferred_compounding=rec.inferred_compounding,
            validations_json=wire_validations(rec.rules),
            data_json={
                n: encode_value(n, rec.value(n), rec.fields[n].raw if n in rec.fields else None) for n in FIELDS
            },
        )
        session.add(db_rec)
        failed_fields = {n for rr in rec.rules if rr.failed for n in rr.fields}
        for name in FIELDS:
            f = rec.fields.get(name) or FieldEvidence(name=name, page=rec.page)
            db_f = ExtractedField(
                id=new_id(),
                record_id=db_rec.id,
                field_name=name,
                raw_value=f.raw,
                normalized_value=encode_value(name, f.value, f.raw),
                data_type=FIELD_TYPES[name],
                unit=FIELD_UNITS.get(name),
                source_type=f.source_type,
                confidence_score=f.confidence,
                agreement=f.agreement,
                grounded=f.grounded,
                witness_a=f.witness_a,
                witness_a_conf=f.witness_a_conf,
                witness_b=f.witness_b,
                relook_values=f.relook_values,
                validators=f.validators,
                verification_status=verification_status(f, name in failed_fields),
                review_status="pending" if name in review_fields else "not_required",
                transformation_history_json=f.transformations,
            )
            session.add(db_f)
            field_ids[f"{rec.key}.{name}"] = db_f.id
            # Evidence = a real source region. Filename hints and derived values get none.
            if f.value is not None and f.bbox and f.source_type in {"table_cell", "page_header"}:
                reading = result.readings.get(f.page)
                ev = Evidence(
                    id=new_id(),
                    document_id=job.document_id,
                    job_id=job.id,
                    page_number=f.page,
                    bounding_box=to_xywh(f.bbox),
                    source_text=f.raw,
                    source_type="native_text" if page_kind.get(f.page) == "digital" else "ocr",
                    provider_metadata={
                        "region_type": f.source_type,
                        "witness_a_provider": reading.provider if reading else None,
                        "extractor": result.extractor_name,
                    },
                )
                session.add(ev)
                session.add(FieldEvidenceLink(field_id=db_f.id, evidence_id=ev.id, relationship_type="value"))
        for rr in rec.rules:
            session.add(_validation_row(job.id, db_rec.id, rr, 1))

    seq = next_seq(session, job.id)
    for ev in result.actions:
        out_refs = list(ev.output_refs)
        for name, data, ctype in ev.artifacts:
            art_id = new_id()
            key = storage.put(f"jobs/{job.id}/artifacts/{art_id}.png", data, ctype)
            session.add(
                Artifact(
                    id=art_id,
                    job_id=job.id,
                    kind="relook_crop" if "crop" in name else "relook_header",
                    storage_key=key,
                    content_type=ctype,
                )
            )
            out_refs.append(f"artifact:{art_id}")
        in_refs = [
            f"field:{field_ids[r[6:]]}" if r.startswith("field:") and r[6:] in field_ids else r for r in ev.input_refs
        ]
        session.add(
            AgentAction(
                job_id=job.id,
                seq=seq,
                trace_id=job.trace_id,
                stage=ev.stage,
                agent_name=ev.agent_name,
                action_type=ev.action_type,
                reason_code=ev.reason_code,
                summary=ev.summary,
                input_refs=in_refs,
                output_refs=out_refs,
                result_status=ev.result_status,
                duration_ms=ev.duration_ms,
                attempt_number=ev.attempt_number,
                model_name=ev.model_name,
                token_usage_json=ev.token_usage,
                estimated_cost_usd=ev.estimated_cost_usd,
            )
        )
        seq += 1

    job.total_latency_ms = result.latency_ms
    job.estimated_cost_usd = result.estimated_cost_usd
    job.provider_calls = result.provider_calls
    session.flush()
    commit_result_version(session, job, "pipeline")
    transition(job, JobStatus.REVIEW_REQUIRED if any(r.needs_review for r in result.records) else JobStatus.COMPLETED)


def _validation_row(job_id: str, record_id: str, rr: RuleResult, version: int) -> ValidationResult:
    return ValidationResult(
        job_id=job_id,
        record_id=record_id,
        field_names=rr.fields,
        rule_id=rr.rule_id,
        rule_version=rr.rule_version,
        severity=rr.severity,
        status=rr.status,
        expected_value=rr.expected,
        actual_value=rr.actual,
        message=rr.message,
        result_version=version,
    )


def commit_result_version(session: Session, job: ExtractionJob, reason: str) -> int:
    """Append an immutable, complete snapshot of the job's results."""
    job.result_version = (job.result_version or 0) + 1
    session.flush()
    session.add(
        ResultVersion(
            job_id=job.id, version=job.result_version, reason=reason, snapshot_json=serialize_results(session, job)
        )
    )
    session.flush()
    return job.result_version


# ------------------------------------------------------------------ serialization (wire)
def serialize_evidence(ev: Evidence) -> dict:
    return {
        "id": ev.id,
        "document_id": ev.document_id,
        "page_number": ev.page_number,
        "bounding_box": ev.bounding_box,
        "source_text": ev.source_text or "",
        "source_type": ev.source_type,
        "preview_url": f"/api/v1/documents/{ev.document_id}/pages/{ev.page_number}/preview",
    }


def serialize_field(f: ExtractedField) -> dict:
    return {
        "id": f.id,
        "field_name": f.field_name,
        "raw_value": f.raw_value,
        "normalized_value": f.normalized_value,
        "evidence": [serialize_evidence(link.evidence) for link in f.evidence_links if link.evidence.bounding_box],
        "witness_a": f.witness_a,
        "witness_b": f.witness_b,
        "review_status": REVIEW_STATUS_WIRE.get(f.review_status, "pending"),
        # additive provenance
        "review_required": f.review_status in {"pending", "in_review", "deferred"},
        "confidence": f"{f.confidence_score:.2f}" if f.normalized_value is not None else None,
        "agreement": f.agreement,
        "grounded": f.grounded,
        "witness_a_conf": f.witness_a_conf,
        "relook_values": f.relook_values or [],
        "validators": f.validators or {},
        "verification_status": f.verification_status,
        "source_type": f.source_type,
        "data_type": f.data_type,
        "unit": f.unit,
        "version": f.version,
        "transformation_history": f.transformation_history_json or [],
    }


def serialize_record(r: ExtractedRecord, document_id: str, result_version: int) -> dict:
    fields = {f.field_name: f for f in r.fields}
    return {
        "id": r.id,
        "document_id": document_id,
        "job_id": r.job_id,
        "source_file": r.source_file,
        "page": r.page_number,
        "values": {n: fields[n].normalized_value if n in fields else None for n in FIELDS},
        "fields": {n: serialize_field(fields[n]) for n in FIELDS if n in fields},
        "status": RECORD_STATUS_WIRE.get(r.status, "needs_review"),
        "validations": r.validations_json or [],
        "version": result_version,
        "extraction_confidence": f"{r.extraction_confidence:.2f}",
        # additive provenance
        "decision": r.decision,
        "review_reasons": r.review_reasons or [],
        "reason_codes": r.reason_codes or [],
        "record_key": r.record_key,
        "record_index": r.record_index,
        "product_label": r.product_label,
        "row_bbox": to_xywh(r.row_bbox),
        "inferred_compounding": r.inferred_compounding,
        "record_version": r.version,
    }


def serialize_results(session: Session, job: ExtractionJob) -> dict:
    records = list(
        session.scalars(
            select(ExtractedRecord).where(ExtractedRecord.job_id == job.id).order_by(ExtractedRecord.record_index)
        )
    )
    data = [serialize_record(r, job.document_id, job.result_version) for r in records]
    return {
        "records": data,
        "result_version": job.result_version,
        "job_id": job.id,
        "document_id": job.document_id,
        "pipeline_version": job.pipeline_version,
        "summary": {
            "records": len(data),
            "validated": sum(1 for r in data if r["status"] == "validated"),
            "needs_review": sum(1 for r in data if r["status"] == "needs_review"),
            "human_verified": sum(1 for r in data if r["status"] == "human_verified"),
            "rejected": sum(1 for r in data if r["status"] == "rejected"),
            "document_inconsistency": sum(1 for r in data if r["decision"] == "DOCUMENT_INCONSISTENCY"),
            "open_review_items": sum(1 for r in data for f in r["fields"].values() if f["review_required"]),
        },
    }


def get_result_version(session: Session, job: ExtractionJob, version: int | None) -> dict:
    if job.result_version == 0:
        raise AppError("RESULTS_NOT_READY", f"job has no results yet (status {job.status})", 409)
    v = version or job.result_version
    snap = session.scalar(select(ResultVersion).where(ResultVersion.job_id == job.id, ResultVersion.version == v))
    if snap is None:
        raise AppError("RESULT_VERSION_NOT_FOUND", f"result version {v} does not exist", 404)
    return snap.snapshot_json


def to_draft(record: ExtractedRecord) -> RecordDraft:
    """Rebuild an in-memory draft from persisted fields (for revalidation after review)."""
    fields = {}
    for f in record.fields:
        fields[f.field_name] = FieldEvidence(
            name=f.field_name,
            value=decode_value(f.field_name, f.normalized_value),
            raw=f.raw_value,
            page=record.page_number,
            witness_a=f.witness_a,
            witness_a_conf=f.witness_a_conf,
            witness_b=f.witness_b,
            agreement=f.agreement,
            grounded=f.grounded,
            source_type=f.source_type,
            confidence=f.confidence_score,
        )
    return RecordDraft(
        key=record.record_key,
        source_file=record.source_file,
        page=record.page_number,
        fields=fields,
        product_label=record.product_label,
    )
