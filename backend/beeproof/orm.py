"""Persistence model (DATA_MODEL.md, open-access variant: no workspaces or users)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beeproof.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ts(nullable: bool = False, default=True):
    return mapped_column(DateTime(timezone=True), nullable=nullable, default=utcnow if default else None)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="stored")
    created_at: Mapped[datetime] = _ts()
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_number"
    )
    jobs: Mapped[list[ExtractionJob]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16))
    preview_key: Mapped[str] = mapped_column(String(512))
    native_text_available: Mapped[bool] = mapped_column(Boolean, default=False)
    layout_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    layout_version: Mapped[str] = mapped_column(String(32), default="1")

    document: Mapped[Document] = relationship(back_populates="pages")


class ExtractionSchema(Base):
    __tablename__ = "extraction_schemas"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()
    versions: Mapped[list[ExtractionSchemaVersion]] = relationship(
        back_populates="schema", cascade="all, delete-orphan", order_by="ExtractionSchemaVersion.version"
    )


class ExtractionSchemaVersion(Base):
    __tablename__ = "extraction_schema_versions"
    __table_args__ = (UniqueConstraint("schema_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schema_id: Mapped[str] = mapped_column(ForeignKey("extraction_schemas.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    schema_json: Mapped[dict] = mapped_column(JSON)
    business_rules_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="published")  # draft | published
    created_at: Mapped[datetime] = _ts()
    schema: Mapped[ExtractionSchema] = relationship(back_populates="versions")


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    schema_version_id: Mapped[str] = mapped_column(ForeignKey("extraction_schema_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    pipeline_version: Mapped[str] = mapped_column(String(32))
    trace_id: Mapped[str] = mapped_column(String(36), default=new_id)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    result_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = _ts(nullable=True, default=False)
    completed_at: Mapped[datetime | None] = _ts(nullable=True, default=False)
    heartbeat_at: Mapped[datetime | None] = _ts(nullable=True, default=False)
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_calls: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    document: Mapped[Document] = relationship(back_populates="jobs")
    records: Mapped[list[ExtractedRecord]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ExtractedRecord.record_index"
    )


class ExtractedRecord(Base):
    __tablename__ = "extracted_records"
    __table_args__ = (UniqueConstraint("job_id", "record_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    record_index: Mapped[int] = mapped_column(Integer)
    record_key: Mapped[str] = mapped_column(String(32))
    source_file: Mapped[str] = mapped_column(String(255))
    page_number: Mapped[int] = mapped_column(Integer)
    product_label: Mapped[str] = mapped_column(String(255), default="")
    row_bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(32)
    )  # auto_approved | needs_review | document_inconsistency | human_verified | rejected
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=True)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)
    reason_codes: Mapped[list] = mapped_column(JSON, default=list)
    inferred_compounding: Mapped[str | None] = mapped_column(String(16), nullable=True)
    data_json: Mapped[dict] = mapped_column(JSON, default=dict)
    validations_json: Mapped[list] = mapped_column(JSON, default=list)  # current rule outcomes (wire shape)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _ts()

    job: Mapped[ExtractionJob] = relationship(back_populates="records")
    fields: Mapped[list[ExtractedField]] = relationship(back_populates="record", cascade="all, delete-orphan")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    __table_args__ = (UniqueConstraint("record_id", "field_name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("extracted_records.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # decimal-as-string
    data_type: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32))
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    agreement: Mapped[float] = mapped_column(Float, default=0.0)
    grounded: Mapped[bool] = mapped_column(Boolean, default=False)
    witness_a: Mapped[str | None] = mapped_column(Text, nullable=True)
    witness_a_conf: Mapped[float | None] = mapped_column(Float, nullable=True)
    witness_b: Mapped[str | None] = mapped_column(Text, nullable=True)
    relook_values: Mapped[list] = mapped_column(JSON, default=list)
    validators: Mapped[dict] = mapped_column(JSON, default=dict)
    verification_status: Mapped[str] = mapped_column(String(32), default="pending")
    review_status: Mapped[str] = mapped_column(String(32), default="not_required")
    version: Mapped[int] = mapped_column(Integer, default=1)
    transformation_history_json: Mapped[list] = mapped_column(JSON, default=list)

    record: Mapped[ExtractedRecord] = relationship(back_populates="fields")
    evidence_links: Mapped[list[FieldEvidenceLink]] = relationship(back_populates="field", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    bounding_box: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(32))
    provider_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class FieldEvidenceLink(Base):
    __tablename__ = "field_evidence"
    field_id: Mapped[str] = mapped_column(ForeignKey("extracted_fields.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True)
    relationship_type: Mapped[str] = mapped_column(String(32), default="value")
    relevance_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    field: Mapped[ExtractedField] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship()


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("extracted_records.id", ondelete="CASCADE"), index=True)
    field_names: Mapped[list] = mapped_column(JSON, default=list)
    rule_id: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8))
    expected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    result_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = _ts()


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_jobs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


class AgentAction(Base):
    """Append-only audit trace (AGENT_DESIGN.md §9)."""

    __tablename__ = "agent_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(36))
    stage: Mapped[str] = mapped_column(String(32))
    agent_name: Mapped[str] = mapped_column(String(64))
    action_type: Mapped[str] = mapped_column(String(64))
    reason_code: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    input_refs: Mapped[list] = mapped_column(JSON, default=list)
    output_refs: Mapped[list] = mapped_column(JSON, default=list)
    result_status: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = _ts()


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("extracted_fields.id", ondelete="CASCADE"), index=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ids_json: Mapped[list] = mapped_column(JSON, default=list)
    institution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _ts()


class ResultVersion(Base):
    """Immutable snapshot of a job's results; exports reference these."""

    __tablename__ = "result_versions"
    __table_args__ = (UniqueConstraint("job_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()


class ExportArtifact(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), index=True)
    result_version: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(8))
    include_evidence_manifest: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _ts()
    completed_at: Mapped[datetime | None] = _ts(nullable=True, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    job: Mapped[ExtractionJob] = relationship()


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pipeline_version: Mapped[str] = mapped_column(String(32))
    configuration_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = _ts(nullable=True, default=False)
    completed_at: Mapped[datetime | None] = _ts(nullable=True, default=False)
    created_at: Mapped[datetime] = _ts()
