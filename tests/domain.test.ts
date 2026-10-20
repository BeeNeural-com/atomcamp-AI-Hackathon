import test from "node:test";
import assert from "node:assert/strict";
import { createDemo, reviewDemo } from "../lib/demo";
import { blockingFailures, exportCsv, csvCell, validateFile, validBox, validateValues } from "../lib/domain";
import type { ReviewInput } from "../lib/types";

test("sample data has 150 records, 30 documents, no claimed model execution", () => {
  const state = createDemo();
  assert.equal(state.records.length, 150);
  assert.equal(state.documents.length, 30);
  assert.ok(state.records.every(r => r.status === "needs_review" && r.extraction_confidence === null));
  assert.ok(state.records.every(r => Object.values(r.fields).every(f => f.witness_a === null && f.witness_b === null)));
});
test("every reconstructed field has an in-bounds preview region", () => {
  for (const r of createDemo().records) for (const f of Object.values(r.fields)) assert.ok(validBox(f.evidence[0].bounding_box), `${r.id}/${f.field_name}`);
});
test("physics rejects known sample discrepancy and accepts valid compounding", () => {
  const values = createDemo().records[0].values;
  const bad = validateValues({ ...values, rate_pct: "1.076", apy_pct: "1.162" });
  assert.equal(bad.find(v => v.rule_id === "APY_PHYSICS_MAX")?.status, "fail");
  const good = validateValues({ ...values, rate_pct: "3.998", apy_pct: "4.057" });
  assert.equal(good.find(v => v.rule_id === "APY_PHYSICS_MAX")?.status, "pass");
});
test("impossible dates, negative/non-numeric rates and fractional terms fail", () => {
  const base = createDemo().records[0].values;
  for (const values of [{ effective_date: "2026-02-30" }, { rate_pct: "-1" }, { rate_pct: "hello" }, { term_months: "1.2" }]) {
    assert.ok(validateValues({ ...base, ...values }).some(v => v.status === "fail" && v.severity === "error"));
  }
});
test("mandatory failures cannot be accepted and source text is immutable after correction", () => {
  const initial = createDemo(), record = initial.records[0];
  const input: ReviewInput = { decision: "accept", corrected_value: null, reason_code: "MANUAL_REVIEW", comment: "Checked source", reviewer_name: "Test", evidence_ids: [], expected_result_version: 1 };
  assert.throws(() => reviewDemo(initial, record.id, "apy_pct", input), /blocking/);
  const result = reviewDemo(initial, record.id, "apy_pct", { ...input, decision: "correct", corrected_value: "1.080" });
  const updated = result.records.find(r => r.id === record.id)!;
  assert.equal(updated.values.apy_pct, "1.080");
  assert.equal(updated.fields.apy_pct.raw_value, "1.162");
  assert.equal(initial.records[0].values.apy_pct, "1.162");
  assert.equal(result.snapshots[`${record.job_id}:1`][0].values.apy_pct, "1.162");
  assert.equal(result.snapshots[`${record.job_id}:2`][0].values.apy_pct, "1.080");
  assert.equal(result.history[0].previous_value, "1.162");
  assert.equal(result.history[0].result_version, 2);
  assert.equal(updated.status, "needs_review");
  assert.equal(blockingFailures(updated).length, 0);
});
test("stale reviews cannot overwrite newer versions", () => {
  const initial = createDemo(), record = initial.records[0];
  const input: ReviewInput = { decision: "defer", corrected_value: null, reason_code: "SOURCE_VERIFICATION_REQUIRED", comment: "Need original PDF", reviewer_name: "", evidence_ids: [], expected_result_version: 1 };
  const updated = reviewDemo(initial, record.id, "apy_pct", input);
  assert.throws(() => reviewDemo(updated, record.id, "apy_pct", input), /changed/);
});
test("CSV preserves decimals, escapes quotes, and neutralizes spreadsheet formulas", () => {
  const record = createDemo().records[0];
  record.values.institution_name = '=HYPERLINK("https://example.com")';
  const csv = exportCsv([record]);
  assert.ok(csv.startsWith("source_file,institution_name,product_type,term_months,rate_pct,apy_pct,min_deposit,effective_date,extraction_confidence,needs_review"));
  assert.ok(csv.includes('"1.076"'));
  assert.ok(csv.includes(`"'=HYPERLINK(""https://example.com"")"`));
  assert.equal(csvCell('hello,"world"'), '"hello,""world"""');
  assert.equal(csvCell(null), '""');
});
test("client upload guards reject unsupported, empty, and oversized files", () => {
  assert.equal(validateFile({ name: "rate.pdf", size: 1000 }), null);
  assert.match(validateFile({ name: "rate.exe", size: 1000 })!, /Choose/);
  assert.match(validateFile({ name: "rate.jpg", size: 0 })!, /empty/);
  assert.match(validateFile({ name: "rate.png", size: 21 * 1024 * 1024 })!, /20 MB/);
});
