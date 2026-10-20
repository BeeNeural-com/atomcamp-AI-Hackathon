export const fieldNames = ["institution_name", "product_type", "term_months", "rate_pct", "apy_pct", "min_deposit", "effective_date"] as const;
export type FieldName = typeof fieldNames[number];
export type JobStatus = "queued" | "preprocessing" | "parsing" | "extracting" | "validating" | "repairing" | "review_required" | "completed" | "failed" | "cancelled";
export type RecordStatus = "validated" | "needs_review" | "human_verified" | "rejected";
export type Section = "overview" | "documents" | "review" | "exports" | "evaluation" | "schemas" | "settings";
export type Box = { x: number; y: number; width: number; height: number; coordinate_space: "normalized_page" };
export type Evidence = { id: string; document_id: string; page_number: number; bounding_box: Box; source_text: string; source_type: "reconstruction" | "native_text" | "ocr" };
export type Validation = { rule_id: string; severity: "error" | "warning" | "info"; status: "pass" | "fail"; message: string; field_names: FieldName[] };
export type ExtractedField = { id: string; field_name: FieldName; raw_value: string | null; normalized_value: string | null; evidence: Evidence[]; witness_a: string | null; witness_b: string | null; review_status: "pending" | "accepted" | "corrected" | "rejected" | "deferred" };
export type RateRecord = {
  id: string; document_id: string; job_id: string; source_file: string; page: number;
  values: Record<FieldName, string | null>; fields: Record<FieldName, ExtractedField>;
  status: RecordStatus; validations: Validation[]; version: number; extraction_confidence: string | null;
};
export type TraceEvent = { id: string; job_id: string; stage: string; summary: string; status: "success" | "warning" | "error"; created_at: string; duration_ms: number | null };
export type DocumentItem = {
  id: string; original_filename: string; mime_type: string; size_bytes: number | null; page_count: number;
  institution_name: string; created_at: string; job_id: string; status: JobStatus; record_count: number; result_version: number; is_demo: boolean;
};
export type Job = { id: string; document_id: string; status: JobStatus; stage: string; progress: number | null; attempt_count: number; error_code: string | null; error_message?: string; trace_id: string; result_version?: number };
export type Results = { records: RateRecord[]; result_version: number };
export type ReviewInput = {
  decision: "accept" | "correct" | "reject" | "defer"; corrected_value: string | null; reason_code: string;
  comment: string; reviewer_name: string; evidence_ids: string[]; expected_result_version: number;
};
export type ReviewHistory = ReviewInput & { id: string; field_id: string; previous_value: string | null; created_at: string; result_version: number };
export type ExportItem = { id: string; job_id: string; document_name: string; result_version: number; format: "csv" | "json"; status: "queued" | "processing" | "ready" | "failed"; created_at: string; error_code?: string | null };
export type SchemaVersion = { id: string; name: string; version: number; status: "published" | "draft"; fields: { name: string; type: string; required: boolean; description: string }[] };
export type EvaluationReport = {
  id: string; status: "queued" | "running" | "completed" | "failed"; dataset_version: string; pipeline_version: string;
  document_count: number; field_count: number; exclusions: number;
  metrics: { name: string; numerator: number; denominator: number; value: number | null }[];
  pipelines?: { name: "native" | "single_pass" | "evidence_first"; field_accuracy: number | null; evidence_coverage: number | null; false_auto_accept_rate: number | null; p95_latency_ms: number | null }[];
  estimated_cost_usd?: string | null;
};
export type DemoState = { documents: DocumentItem[]; records: RateRecord[]; traces: TraceEvent[]; history: ReviewHistory[]; exports: ExportItem[]; snapshots: Record<string, RateRecord[]> };
