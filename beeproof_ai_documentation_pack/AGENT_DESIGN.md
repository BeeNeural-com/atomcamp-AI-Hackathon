# Agent Design and Orchestration

## 1. Design principle

Use an explicit, typed state machine. Agents propose actions;
deterministic domain services enforce invariants. The system must be
able to explain what action occurred and why without exposing hidden
chain-of-thought.

## 2. Supervisor responsibilities

-   Read current job state.
-   Determine the next eligible stage.
-   Enforce global attempt, latency, and spend budgets.
-   Select approved tools.
-   Prevent repeated actions without new information.
-   Route unresolved ambiguity to human review.
-   Record a concise decision summary.
-   Never bypass the acceptance gate.

## 3. Agent roles

### Layout agent

Inputs: page metadata, native text signals, parser outputs.\
Outputs: layout quality indicators, suspicious regions, recommended
parsing action.

### Extraction agent

Inputs: schema version, relevant table/text regions, header map,
footnotes, allowed normalization rules.\
Outputs: candidate values, raw text, candidate evidence IDs, ambiguity
markers.

### Verification agent

Inputs: candidate values, schema, evidence, deterministic rules.\
Outputs: validation results and blocking/non-blocking findings.

### Repair planner

Inputs: validation failures, source artifacts, previous attempts,
remaining budgets.\
Outputs: one allowlisted repair action or escalation.

### Review router

Inputs: unresolved fields, severity, evidence sufficiency, retry
budget.\
Outputs: review task and reason code.

## 4. Allowed repair tools

-   `rerender_region(region_id, scale_factor)`
-   `deskew_page(page_number)`
-   `reparse_page(page_number, parser_profile)`
-   `reprocess_ocr(region_id, options)`
-   `inspect_header(table_id, column_index)`
-   `reconstruct_row(table_id, row_index)`
-   `reextract_region(region_id, schema_version_id, prompt_profile)`
-   `request_human_review(field_id, reason_code)`

Each tool must validate IDs, dimensions, option ranges, and
budget before execution. Do not provide arbitrary shell, filesystem,
SQL, or unrestricted HTTP tools to an LLM.

## 5. Repair selection policy

Use deterministic mappings where possible:

-   Missing evidence → inspect source region or re-extract with evidence
    requirement.
-   Numeric parse failure → inspect the source crop and neighboring
    cells.
-   Unit mismatch → inspect header and footnote context.
-   Cross-field arithmetic failure → inspect all participating cells and
    formula assumptions.
-   Low-resolution OCR → rerender at a higher scale within resource
    limits.
-   Rotated page → deskew/rotate and reparse.
-   Ambiguous decimal or sign → targeted OCR plus human review if
    unresolved.
-   Unknown schema mapping → human review or explicit unmapped state.

Do not automatically change a value merely because it makes a total
reconcile. Reconciliation is a validation signal, not proof of the
correct source value.

## 6. Budgets

Initial configurable defaults: - Up to two targeted repair attempts per
field/failure group. - Up to one full-page reprocessing attempt per page
unless explicitly approved. - Up to three major recovery attempts per
job. - Maximum provider calls, processing time, and estimated cost per
document.

These are starting defaults, not universal optimal values. Benchmark and
tune them. Avoid nested retry loops that multiply calls unexpectedly.

## 7. State transitions

``` text
queued -> preprocessing -> parsing -> extracting -> validating
validating -> completed
validating -> repairing -> validating
validating -> review_required
review_required -> validating (after reviewer action)
any non-terminal state -> failed (permanent error)
eligible failed job -> queued only through explicit retry/run action
non-terminal state -> cancelled when safe
```

Implement transitions in one authoritative service. Reject invalid
transitions and test them.

## 8. Idempotency and recovery

-   Queue messages carry job/run IDs only.
-   Duplicate messages must not duplicate final records.
-   Stage outputs are identified by job, stage, input artifact version,
    and configuration hash.
-   A worker must claim a job atomically.
-   Stale processing jobs need a recovery policy.
-   Transient errors use bounded exponential backoff.
-   Permanent errors fail without repeated retries.
-   Each repair creates an action event and a new artifact/result
    version.

## 9. Audit trace format

Each event should include: - event ID; - job ID; - stage; - action
type; - concise reason code and summary; - input artifact references; -
output artifact references; - status; - duration; - provider/model
identifier if relevant; - attempt number; - cost/token metadata where
available; - timestamp.

Never store unrestricted model reasoning as an audit requirement.
Summaries should describe observable actions and evidence.

## 10. Acceptance gate

A field can be auto-accepted only if: - required evidence exists and
resolves; - schema and type checks pass; - all blocking deterministic
rules pass; - ambiguity is below configured policy thresholds; - the job
has not exhausted a required review condition; - the applicable
acceptance policy version is recorded.

Model confidence alone is insufficient. A document-level completion
status must not hide fields requiring review.
