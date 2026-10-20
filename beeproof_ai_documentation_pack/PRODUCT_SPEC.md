# Product Specification

## 1. Purpose

BeeProof AI converts unstructured documents---primarily rate-sheet PDFs
and images---into structured, validated, exportable records while
preserving source evidence and audit history.

## 2. Problem

Manual extraction is slow and vulnerable to transcription errors. Plain
OCR can identify text without understanding table relationships,
headers, units, footnotes, or multi-page continuity. LLM-only extraction
can produce plausible but unsupported values and may silently normalize
or infer missing data.

BeeProof AI addresses these limitations with a pipeline that separates
perception, extraction, validation, repair, and acceptance.

## 3. Product goals

1.  Extract schema-conformant records from supported PDF and image
    inputs.
2.  Link each field to one or more source evidence regions.
3.  Validate values using deterministic, versioned rules.
4.  Repair recoverable errors through bounded actions.
5.  Route unresolved ambiguity to a human reviewer.
6.  Preserve every extraction and correction version.
7.  Export results to CSV and JSON.
8.  Measure accuracy, evidence coverage, false auto-accepts, review
    rate, latency, and cost.
9.  Run locally and deploy to GCP using reproducible configuration.

## 4. Non-goals for the first release

-   General-purpose document understanding for every industry.
-   Automatic model fine-tuning from unreviewed predictions.
-   Unrestricted autonomous tool execution.
-   Guaranteed zero-error extraction.
-   Real-time processing of arbitrarily large documents.
-   A vector database unless a concrete retrieval requirement emerges.
-   A microservice per agent.
-   Kubernetes deployment.
-   User accounts, authentication, authorization, roles, or
    multi-tenant workspaces.

## 5. Primary users

These are personas, not enforced roles. The application is open: there
is no login and no permission check, and anyone who can reach the URL
can perform every action.

### Operator

Uploads documents, selects a schema, tracks jobs, and exports results.

### Reviewer

Inspects uncertain fields, opens source evidence, corrects values, and
records decisions.

### Administrator

Manages schemas, retention settings, provider configuration, and usage
limits.

### Evaluator

Runs benchmark datasets, compares pipeline versions, and reviews
measurable performance.

## 6. Core user journeys

### 6.1 Upload and process

1.  User selects a supported file.
2.  Server validates file content and limits.
3.  Original file is stored using a server-generated identifier.
4.  User selects a schema version.
5.  A processing job is queued.
6.  UI displays job status and stage progress.
7.  Results become available when processing reaches a terminal state.

### 6.2 Inspect a field

1.  User opens a document view.
2.  User selects a field or table cell.
3.  UI retrieves the field, evidence references, and source page
    preview.
4.  UI highlights the evidence region.
5.  User can inspect raw value, normalized value, unit, validation
    results, and action history.

### 6.3 Repair and review

1.  Validation identifies a recoverable failure.
2.  The repair planner selects an allowed action within budget.
3.  The targeted region is reprocessed.
4.  The result is revalidated.
5.  If unresolved, the field is routed to human review.
6.  A reviewer accepts, corrects, rejects, or flags the field.
7.  The system stores a new version and audit event.

### 6.4 Export

1.  User selects a completed result version.
2.  Export service validates the job status.
3.  CSV or JSON is generated from the selected immutable result version.
4.  Export metadata and generation status are recorded.
5.  User downloads the artifact.

### 6.5 Evaluate

1.  Evaluator selects a dataset and pipeline configuration.
2.  The benchmark runner processes the dataset.
3.  Metrics are calculated against known labels.
4.  A report stores the dataset version, configuration, code revision,
    and results.
5.  Baseline and enhanced pipeline metrics are compared using consistent
    cases.

## 7. Functional requirements

### FR-01 File upload

Accept configured PDF and image types. Enforce size, page-count, and
pixel limits. Validate file signatures rather than relying only on
extension or MIME type.

### FR-02 Document storage

Persist original files separately from database metadata. Use object
storage with server-generated keys and stable document IDs.

### FR-03 Job orchestration

Process jobs asynchronously. Expose status, stage, retry count, error
code, and timestamps.

### FR-04 Parsing

Extract native PDF text where useful and use OCR/layout parsing for
scanned or visually complex content.

### FR-05 Table reconstruction

Preserve table, row, cell, header, merged-cell, page, and footnote
relationships where detectable.

### FR-06 Schema-aware extraction

Return structured candidate values that conform to a versioned schema.
Distinguish absent, illegible, ambiguous, and not-applicable values.

### FR-07 Evidence

Store source text and page/region coordinates. Each evidence reference
must resolve to a persisted artifact.

### FR-08 Validation

Run deterministic field, record, and cross-record rules. Persist rule
version, severity, result, and explanation.

### FR-09 Risk-based routing

Route to acceptance, repair, or review using explicit gates. A model
confidence claim cannot override a mandatory validation failure.

### FR-10 Repair

Use an allowlisted set of actions and a global retry/cost budget. Record
reason, inputs, outputs, duration, and outcome.

### FR-11 Human review

Allow reviewers to inspect evidence and correct values.
Preserve old values and decision metadata.

### FR-12 Export

Support JSON and CSV with stable column definitions, explicit null
handling, and documented decimal/unit representation.

### FR-13 Audit

Provide a chronological trace of executed stages, tool calls, validation
results, repair actions, and review decisions.

### FR-14 Evaluation

Run reproducible benchmarks and report the dataset/configuration version
alongside metrics.

### FR-15 Administration

Support schema versioning, configurable limits, and provider settings.

## 8. Non-functional requirements

-   Reliability: queue delivery may occur more than once; processing
    must be idempotent.
-   Access: open by design; no authentication or authorization.
    Security effort goes to file safety, prompt-injection resistance,
    and resource limits.
-   Traceability: every result has a document, job, pipeline version,
    and source evidence.
-   Maintainability: provider-specific code is isolated behind adapters.
-   Testability: deterministic tests do not require live model calls.
-   Portability: local and production deployments share core domain
    logic.
-   Cost control: enforce file, page, call, retry, and per-job spend
    limits.
-   Observability: emit structured logs and trace identifiers.
-   Accessibility: keyboard navigation, clear status labels, and
    adequate contrast.
-   Data integrity: corrections append new versions rather than
    overwrite history.

## 9. P0 scope

-   PDF/image upload.
-   One supported extraction schema for rate sheets.
-   Asynchronous processing.
-   Native text extraction and OCR/layout parsing.
-   Structured extraction.
-   Evidence highlighting.
-   Deterministic validation.
-   Bounded repair.
-   Human-review queue.
-   CSV/JSON export.
-   Action timeline.
-   Benchmark runner.
-   Local Docker Compose.
-   GCP deployment path.
-   End-to-end tests and demo fixtures.

## 10. Acceptance criteria

The first release is accepted only when:

1.  A developer can start the local stack from documented commands.
2.  A supported PDF and a supported image can be uploaded.
3.  A job runs asynchronously and exposes meaningful state.
4.  The system produces schema-conformant records.
5.  Evidence references resolve to correct source pages and regions.
6.  Validation failures are persisted and visible.
7.  Repair attempts are bounded and auditable.
8.  Unresolved fields are routed to human review.
9.  Human corrections preserve history and trigger revalidation.
10. CSV and JSON exports match the selected result version.
11. The action timeline reflects actual execution.
12. The benchmark is reproducible.
13. Unit, integration, and end-to-end tests pass.
14. Deployment and known limitations are documented.
15. No unmeasured accuracy claims appear in the product or presentation.
