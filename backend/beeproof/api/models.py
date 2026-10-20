"""Typed API contracts. Field names and enums match the frontend's `lib/types.ts`;
keys marked "additive" are extra provenance the UI may ignore."""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_serializer

# `schema_json` is the field name API_CONTRACTS.md uses; it shadows a deprecated BaseModel method.
warnings.filterwarnings("ignore", message='Field name "schema_json"', category=UserWarning)

T = TypeVar("T")
JobStatus = Literal[
    "queued",
    "preprocessing",
    "parsing",
    "extracting",
    "validating",
    "repairing",
    "review_required",
    "completed",
    "failed",
    "cancelled",
]
FieldName = Literal[
    "institution_name", "product_type", "term_months", "rate_pct", "apy_pct", "min_deposit", "effective_date"
]


class Meta(BaseModel):
    request_id: str
    next_cursor: Optional[str] = None


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta


class ErrorDetail(BaseModel):
    field: Optional[str] = None
    reason: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------- documents
class DocumentItem(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    size_bytes: Optional[int]
    page_count: int
    institution_name: str = Field(description="From the latest run's results; '' until extracted")
    created_at: datetime
    job_id: str = Field(description="Latest run; '' when no job exists")
    status: JobStatus = Field(description="Latest run status; 'queued' when no job exists")
    record_count: int
    result_version: int
    is_demo: bool = False
    sha256: Optional[str] = None  # additive
    duplicate_of: Optional[str] = None  # additive


class PageOut(BaseModel):
    page_number: int
    width: int
    height: int
    rotation: int
    kind: Literal["digital", "scanned", "image"]
    native_text_available: bool
    layout_provider: Optional[str] = None
    preview_url: str


# ---------------------------------------------------------------- schemas
class SchemaFieldOut(BaseModel):
    name: str
    type: str
    required: bool
    description: str


class SchemaVersionOut(BaseModel):
    id: str = Field(description="Schema version id; pass as schema_version_id when creating a job")
    name: str
    version: int
    status: Literal["published", "draft"]
    fields: list[SchemaFieldOut]
    schema_id: Optional[str] = None  # additive
    business_rules_json: Optional[dict[str, Any]] = None


class SchemaOut(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    versions: list[SchemaVersionOut] = []


class SchemaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    schema_json: Optional[dict[str, Any]] = None
    business_rules_json: Optional[dict[str, Any]] = None


class SchemaVersionCreate(BaseModel):
    schema_json: dict[str, Any]
    business_rules_json: dict[str, Any] = {}
    status: Literal["draft", "published"] = "published"


class SchemaValidateIn(BaseModel):
    schema_json: dict[str, Any]


class SchemaValidateOut(BaseModel):
    valid: bool
    details: list[ErrorDetail] = []


# ---------------------------------------------------------------- jobs
class JobOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_repair: bool = True
    require_evidence: bool = True
    witness_a: bool = True
    use_memory: bool = True


class JobCreate(BaseModel):
    schema_version_id: Optional[str] = Field(None, description="Defaults to the published rate-sheet schema")
    options: JobOptions = JobOptions()
    idempotency_key: Optional[str] = Field(None, max_length=128)


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str


class JobOut(BaseModel):
    id: str
    document_id: str
    status: JobStatus
    stage: str
    progress: Optional[float] = Field(None, description="Always null: progress is not measurable per stage")
    attempt_count: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    trace_id: str
    result_version: int
    # additive
    schema_version_id: str
    max_attempts: int
    retry_of: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    pipeline_version: str
    total_latency_ms: Optional[int] = None
    estimated_cost_usd: Optional[str] = Field(None, description="null when provider pricing is unknown")
    provider_calls: int
    cancel_requested: bool
    configuration_snapshot: dict[str, Any]
    summary: Optional[dict[str, int]] = None

    @model_serializer(mode="wrap")
    def _omit_empty_error_message(self, handler):
        # lib/types.ts declares `error_message?: string` (absent, never null)
        data = handler(self)
        if data.get("error_message") is None:
            data.pop("error_message", None)
        return data


# ---------------------------------------------------------------- results
class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float
    coordinate_space: Literal["normalized_page"] = "normalized_page"


class EvidenceOut(BaseModel):
    id: str
    document_id: str
    page_number: int
    bounding_box: BoundingBox
    source_text: str
    source_type: Literal["native_text", "ocr"]
    preview_url: str  # additive


class ValidationWire(BaseModel):
    rule_id: str
    severity: Literal["error", "warning", "info"]
    status: Literal["pass", "fail"]
    message: str
    field_names: list[str]
    rule_version: Optional[str] = None  # additive
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None


class FieldOut(BaseModel):
    id: str
    field_name: FieldName
    raw_value: Optional[str] = None
    normalized_value: Optional[str] = Field(None, description="decimal string, integer string, ISO date or enum")
    evidence: list[EvidenceOut]
    witness_a: Optional[str] = None
    witness_b: Optional[str] = None
    review_status: Literal["pending", "accepted", "corrected", "rejected", "deferred"]
    # additive
    review_required: bool
    confidence: Optional[str] = None
    agreement: float
    grounded: bool
    witness_a_conf: Optional[float] = None
    relook_values: list[str] = []
    validators: dict[str, str] = {}
    verification_status: str
    source_type: str
    data_type: str
    unit: Optional[str] = None
    version: int
    transformation_history: list[dict[str, Any]] = []


class RateRecordOut(BaseModel):
    id: str
    document_id: str
    job_id: str
    source_file: str
    page: int
    values: dict[FieldName, Optional[str]]
    fields: dict[FieldName, FieldOut]
    status: Literal["validated", "needs_review", "human_verified", "rejected"]
    validations: list[ValidationWire]
    version: int
    extraction_confidence: Optional[str] = None
    # additive
    decision: Literal["AUTO_APPROVED", "NEEDS_REVIEW", "DOCUMENT_INCONSISTENCY"]
    review_reasons: list[str] = []
    reason_codes: list[str] = []
    record_key: str
    record_index: int
    product_label: str
    row_bbox: Optional[BoundingBox] = None
    inferred_compounding: Optional[str] = None
    record_version: int


class ResultsOut(BaseModel):
    records: list[RateRecordOut]
    result_version: int
    job_id: Optional[str] = None  # additive
    document_id: Optional[str] = None
    pipeline_version: Optional[str] = None
    summary: Optional[dict[str, int]] = None


class ValidationOut(BaseModel):
    id: str
    record_id: str
    record_key: Optional[str] = None
    field_names: list[str]
    rule_id: str
    rule_version: str
    severity: Literal["error", "warning", "info"]
    status: Literal["pass", "fail"]
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    message: str
    result_version: int


class TraceEventOut(BaseModel):
    id: str
    job_id: str
    stage: str
    summary: str
    status: Literal["success", "warning", "error"]
    created_at: datetime
    duration_ms: Optional[int] = None
    # additive
    seq: int
    trace_id: str
    agent_name: str
    action_type: str
    reason_code: str
    input_refs: list[str] = []
    output_refs: list[str] = []
    artifact_urls: list[str] = []
    attempt_number: int
    model_name: Optional[str] = None
    token_usage: Optional[dict[str, Any]] = None
    estimated_cost_usd: Optional[float] = None


# ---------------------------------------------------------------- review
class ReviewQueueItem(BaseModel):
    field: FieldOut
    record_id: str
    record_index: int
    record_key: str
    product_label: str
    page_number: int
    decision: str
    record_values: dict[str, Optional[str]]
    reasons: list[str]


class ReviewIn(BaseModel):
    decision: Literal["accept", "correct", "reject", "defer"]
    corrected_value: Optional[str] = None
    reason_code: Optional[str] = Field(None, max_length=64)
    comment: Optional[str] = Field(None, max_length=2000)
    reviewer_name: Optional[str] = Field(None, max_length=128, description="Optional, unverified (open access)")
    evidence_ids: list[str] = []
    expected_result_version: Optional[int] = Field(None, description="409 STALE_RESULT_VERSION if outdated")


class ReviewHistoryOut(BaseModel):
    id: str
    field_id: str
    decision: Literal["accept", "correct", "reject", "defer"]
    corrected_value: Optional[str] = None
    previous_value: Optional[str] = None
    reason_code: str
    comment: str
    reviewer_name: str
    evidence_ids: list[str]
    expected_result_version: Optional[int] = None
    result_version: int
    created_at: datetime


# ---------------------------------------------------------------- exports
class ExportIn(BaseModel):
    format: Literal["csv", "json"]
    result_version: Optional[int] = Field(None, description="Defaults to the latest result version")
    include_evidence_manifest: bool = False


class ExportItem(BaseModel):
    id: str
    job_id: str
    document_name: str
    result_version: int
    format: Literal["csv", "json"]
    status: Literal["queued", "processing", "ready", "failed"]
    created_at: datetime
    error_code: Optional[str] = None
    # additive
    include_evidence_manifest: bool
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    completed_at: Optional[datetime] = None
    download_url: Optional[str] = None
    manifest_url: Optional[str] = None


# ---------------------------------------------------------------- evaluation
class EvaluationIn(BaseModel):
    dataset_version: Optional[str] = None
    configurations: Optional[list[str]] = Field(
        None,
        description="native, single_pass, evidence_first (frontend) or extractor_only, validators, "
        "validators_witness_a, full (ablation)",
    )
    configs: Optional[list[str]] = Field(None, description="alias of configurations")
    variants: Optional[list[Literal["digital", "scan", "photo"]]] = None
    limit: Optional[int] = Field(None, ge=1, le=500)


class MetricOut(BaseModel):
    name: str
    numerator: int
    denominator: int
    value: Optional[float] = None


class PipelineMetricsOut(BaseModel):
    name: Literal["native", "single_pass", "evidence_first"]
    field_accuracy: Optional[float] = None
    evidence_coverage: Optional[float] = None
    false_auto_accept_rate: Optional[float] = None
    p95_latency_ms: Optional[float] = None
    skipped: Optional[str] = None  # additive


class EvaluationReportOut(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    dataset_version: str
    pipeline_version: str
    document_count: int
    field_count: int
    exclusions: int
    metrics: list[MetricOut]
    pipelines: list[PipelineMetricsOut] = []
    estimated_cost_usd: Optional[str] = None
    # additive
    primary_pipeline: Optional[str] = None
    configurations: list[str] = []
    variants: list[str] = []
    notes: list[str] = []
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    results: dict[str, Any] = {}


class HealthOut(BaseModel):
    status: str
    version: str
    extractor: str
    layout_provider: str
    store: str
    database: str
    database_ok: bool
    queue: str
    queue_ok: bool
    worker_mode: str
    access: Literal["open"] = "open"
