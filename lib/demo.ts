import samples from "./fixtures/sample-records.json";
import { fieldNames, type DemoState, type RateRecord, type FieldName, type Box, type ReviewInput, type SchemaVersion } from "./types";
import { validateValues, blockingFailures } from "./domain";

export const DEMO_STORAGE_KEY = "beeproof-demo-v1";
export const demoSchema: SchemaVersion = {
  id: "00000000-0000-4000-8000-000000000001", name: "Deposit rate sheet", version: 1, status: "published",
  fields: fieldNames.map(name => ({ name, type: ["rate_pct", "apy_pct", "min_deposit"].includes(name) ? "decimal" : name === "term_months" ? "integer" : name === "effective_date" ? "date" : "string", required: !["rate_pct", "apy_pct", "min_deposit"].includes(name), description: ({
    institution_name: "Name exactly as printed on the document.", product_type: "Normalized deposit product category.",
    term_months: "Certificate duration in months; 0 for non-term products.", rate_pct: "Nominal rate in percentage points (4.25 means 4.25%).",
    apy_pct: "Annual percentage yield in percentage points.", min_deposit: "Minimum opening deposit in USD.",
    effective_date: "Effective date in ISO YYYY-MM-DD format.",
  })[name] })),
};
export function cellBox(field: FieldName, index: number): Box {
  const common = { coordinate_space: "normalized_page" as const };
  if (field === "institution_name") return { ...common, x: 0.07, y: 0.10, width: 0.86, height: 0.05 };
  // Each row has its own date; the supplied CSV groups can contain multiple effective dates.
  const xs = { product_type: [0.07, 0.24], term_months: [0.32, 0.10], rate_pct: [0.43, 0.11], apy_pct: [0.55, 0.11], min_deposit: [0.67, 0.12], effective_date: [0.80, 0.14] };
  const [x, width] = xs[field];
  return { ...common, x, y: 0.29 + index * 0.065, width, height: 0.045 };
}
export function createDemo(): DemoState {
  const groups = new Map<string, typeof samples>();
  samples.forEach(row => groups.set(row.source_file, [...(groups.get(row.source_file) ?? []), row]));
  const state: DemoState = { documents: [], records: [], traces: [], history: [], exports: [], snapshots: {} };
  let number = 0;
  for (const [filename, rows] of groups) {
    const docId = `demo-document-${++number}`, jobId = `demo-job-${number}`;
    const created = "2026-10-20T05:00:00.000Z";
    state.documents.push({ id: docId, job_id: jobId, original_filename: filename, mime_type: "application/pdf", size_bytes: null, page_count: 1, institution_name: rows[0].institution_name, created_at: created, status: "review_required", record_count: rows.length, result_version: 1, is_demo: true });
    const records: RateRecord[] = rows.map((row, i) => {
      const id = `${docId}-record-${i + 1}`;
      const values = Object.fromEntries(fieldNames.map(f => [f, row[f] || null])) as RateRecord["values"];
      return {
        id, document_id: docId, job_id: jobId, source_file: filename, page: 1, values, status: "needs_review", validations: validateValues(values), version: 1, extraction_confidence: null,
        fields: Object.fromEntries(fieldNames.map(field => [field, {
          id: `${id}-${field}`, field_name: field, raw_value: values[field], normalized_value: values[field], review_status: "pending",
          witness_a: null, witness_b: null,
          evidence: [{ id: `${id}-evidence-${field}`, document_id: docId, page_number: 1, bounding_box: cellBox(field, i), source_text: values[field] ?? "", source_type: "reconstruction" }],
        }])) as RateRecord["fields"],
      };
    });
    state.records.push(...records);
    state.snapshots[`${jobId}:1`] = structuredClone(records);
    state.traces.push(
      { id: `${jobId}-import`, job_id: jobId, stage: "sample_import", summary: `${rows.length} records loaded from the supplied CSV. Original documents were not supplied.`, status: "success", created_at: created, duration_ms: null },
      { id: `${jobId}-validate`, job_id: jobId, stage: "local_validation", summary: "Client-side rate, APY, date, and term checks applied. No OCR or model calls were made.", status: records.some(r => blockingFailures(r).length) ? "warning" : "success", created_at: created, duration_ms: null },
      { id: `${jobId}-evidence`, job_id: jobId, stage: "review_required", summary: "Preview reconstructed from sample values. Source verification is still required.", status: "warning", created_at: created, duration_ms: null },
    );
  }
  return state;
}
export function reviewDemo(state: DemoState, recordId: string, field: FieldName, input: ReviewInput): DemoState {
  const next = structuredClone(state), record = next.records.find(r => r.id === recordId);
  if (!record) throw new Error("Record no longer exists.");
  const doc = next.documents.find(d => d.id === record.document_id)!;
  if (doc.result_version !== input.expected_result_version) throw new Error("This result has changed. Reopen the record before reviewing.");
  if (!input.comment.trim()) throw new Error("Add a reason so this decision can be audited.");
  const previous = record.values[field];
  if (input.decision === "correct") {
    record.values[field] = input.corrected_value?.trim() || null;
    record.fields[field].normalized_value = record.values[field];
    record.validations = validateValues(record.values);
    if (blockingFailures(record).some(v => v.field_names.includes(field))) throw new Error("The correction still fails a blocking rule. Check the validation details.");
    // Re-open related fields; a previous acceptance cannot survive a changed cross-field assumption.
    const affected = new Set(record.validations.filter(v => v.field_names.includes(field)).flatMap(v => v.field_names));
    affected.forEach(f => { if (f !== field) record.fields[f].review_status = "pending"; });
  }
  if (input.decision === "accept" && blockingFailures(record).some(v => v.field_names.includes(field))) throw new Error("This field has a blocking validation failure. Correct it or defer it.");
  record.fields[field].review_status = ({ accept: "accepted", correct: "corrected", reject: "rejected", defer: "deferred" } as const)[input.decision];
  record.status = Object.values(record.fields).some(f => f.review_status === "rejected") ? "rejected" : Object.values(record.fields).every(f => ["accepted", "corrected"].includes(f.review_status)) && blockingFailures(record).length === 0 ? "human_verified" : "needs_review";
  doc.result_version += 1;
  // A result version is a complete job snapshot, including unchanged records.
  next.records.filter(r => r.job_id === record.job_id).forEach(r => { r.version = doc.result_version; });
  doc.status = next.records.some(r => r.job_id === record.job_id && (r.status === "needs_review" || r.status === "rejected")) ? "review_required" : "completed";
  const created_at = new Date().toISOString();
  next.history.push({ ...input, id: crypto.randomUUID(), field_id: record.fields[field].id, previous_value: previous, created_at, result_version: doc.result_version });
  next.traces.push({ id: crypto.randomUUID(), job_id: record.job_id, stage: `review_${input.decision}`, summary: `${field}: ${previous ?? "null"} → ${record.values[field] ?? "null"}. ${input.comment} (local demo)`, status: input.decision === "reject" || input.decision === "defer" ? "warning" : "success", created_at, duration_ms: null });
  next.snapshots[`${record.job_id}:${doc.result_version}`] = structuredClone(next.records.filter(r => r.job_id === record.job_id));
  return next;
}
