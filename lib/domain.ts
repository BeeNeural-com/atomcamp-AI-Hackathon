import { fieldNames, type FieldName, type RateRecord, type Validation, type Box } from "./types";

export const labels: Record<FieldName, string> = {
  institution_name: "Institution", product_type: "Product", term_months: "Term",
  rate_pct: "Interest rate", apy_pct: "APY", min_deposit: "Minimum deposit", effective_date: "Effective date",
};
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
export function validateFile(file: { name: string; size: number }) {
  if (!/\.(pdf|png|jpe?g|tiff?)$/i.test(file.name)) return "Choose a PDF, PNG, JPG, or TIFF file.";
  if (file.size === 0) return "This file is empty. Choose a valid document.";
  if (file.size > MAX_UPLOAD_BYTES) return "This file exceeds the 20 MB upload limit.";
  return null;
}
export function validateValues(v: RateRecord["values"]): Validation[] {
  const rules: Validation[] = [];
  const add = (rule_id: string, ok: boolean, message: string, fields: FieldName[], severity: Validation["severity"] = "error") =>
    rules.push({ rule_id, status: ok ? "pass" : "fail", severity, message, field_names: fields });
  const numeric = (key: FieldName) => v[key] !== null && v[key] !== "" && /^\d+(\.\d+)?$/.test(v[key]!) && Number.isFinite(Number(v[key]));
  for (const f of ["institution_name", "product_type", "effective_date"] as FieldName[]) add(`REQUIRED_${f.toUpperCase()}`, !!v[f]?.trim(), `${labels[f]} is required.`, [f]);
  add("MISSING_RATE", numeric("rate_pct") || numeric("apy_pct"), "An interest rate or APY is required.", ["rate_pct", "apy_pct"]);
  for (const key of ["rate_pct", "apy_pct", "min_deposit", "term_months"] as FieldName[]) {
    if (v[key] === null) continue;
    const max = key === "min_deposit" ? 10000000 : key === "term_months" ? 120 : 15;
    add(`RANGE_${key.toUpperCase()}`, numeric(key) && Number(v[key]) <= max && (key !== "term_months" || Number.isInteger(Number(v[key]))), `${labels[key]} must be between 0 and ${max.toLocaleString()}.`, [key]);
  }
  const date = v.effective_date ?? "";
  add("FORMAT_DATE", /^\d{4}-\d{2}-\d{2}$/.test(date) && !Number.isNaN(Date.parse(date)) && new Date(date).toISOString().slice(0, 10) === date, "Use a valid date in YYYY-MM-DD format.", ["effective_date"]);
  if (numeric("rate_pct") && numeric("apy_pct")) {
    const rate = Number(v.rate_pct), apy = Number(v.apy_pct), max = Math.expm1(rate / 100) * 100;
    add("APY_GE_RATE", apy >= rate - 0.0005, `APY ${apy}% must be at least the interest rate ${rate}%.`, ["rate_pct", "apy_pct"]);
    add("APY_PHYSICS_MAX", apy <= max + 0.0015, `APY ${apy}% exceeds the ${max.toFixed(3)}% compounding ceiling for a ${rate}% rate. Check product assumptions and source evidence.`, ["rate_pct", "apy_pct"]);
  }
  const termProduct = ["CD", "IRA CD"].includes(v.product_type ?? "");
  add("TERM_PRODUCT_CONSISTENT", numeric("term_months") && (termProduct ? Number(v.term_months) > 0 : Number(v.term_months) === 0), termProduct ? "A certificate requires a positive term." : "A non-term product must have a zero-month term.", ["product_type", "term_months"]);
  return rules;
}
export function blockingFailures(record: RateRecord) { return record.validations.filter(r => r.status === "fail" && r.severity === "error"); }
export function formatValue(field: FieldName, value: string | null) {
  if (value === null || value === "") return "—";
  if (field === "rate_pct" || field === "apy_pct") return `${value}%`;
  if (field === "min_deposit") return `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  if (field === "term_months") return value === "0" ? "No term" : `${value} mo`;
  return value;
}
export function validBox(b: Box) { return [b.x, b.y, b.width, b.height].every(Number.isFinite) && b.x >= 0 && b.y >= 0 && b.width > 0 && b.height > 0 && b.x + b.width <= 1.000001 && b.y + b.height <= 1.000001; }
export function csvCell(value: unknown) {
  let text = value == null ? "" : String(value);
  // Prevent spreadsheet formula execution when exports contain arbitrary document text.
  if (/^[\s]*[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}
export function exportCsv(records: RateRecord[]) {
  const columns = ["source_file", ...fieldNames, "extraction_confidence", "needs_review", "decision", "record_id", "result_version"];
  return [columns.join(","), ...records.map(r => [r.source_file, ...fieldNames.map(f => r.values[f]), r.extraction_confidence, ["needs_review", "rejected"].includes(r.status), r.status, r.id, r.version].map(csvCell).join(","))].join("\r\n");
}
export function downloadFile(content: string | Blob, name: string, mime = "application/json") {
  const url = URL.createObjectURL(content instanceof Blob ? content : new Blob([content], { type: mime }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
