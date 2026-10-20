# Claude: build the BeeProof AI backend

The frontend is implemented in this repository. Your assignment is to build and test the backend that connects to it. Keep the existing React UI. Do not rebuild it in Streamlit or change the public contracts without updating the adapter and its tests.

## Start here

1. Read this file, `lib/types.ts`, `lib/api.ts`, and `docs/product/`.
2. Read `docs/architecture.md` sections 6–8 and 11–12 for extraction, independent witnesses, re-look, deterministic rate rules, and benchmark generation.
3. Inspect Git status. The original architecture, documentation pack, and sample CSV were untracked when frontend work began; preserve them.
4. Start with an upload → durable job → real page preview → typed extraction → validation → review → versioned export vertical slice.
5. Keep all cloud calls behind adapters. Default development tests to fake providers. Implement a real native PDF path and real provider integrations; never pass fixture output off as model execution.

## Resolved specification conflicts

The docs contain two designs. Use these decisions:

- Product brand: **BeeProof AI by BeeNeural**.
- Frontend: existing Next.js 16 / React 19 / TypeScript app at repository root.
- API: FastAPI + Pydantic v2, `/api/v1`, with `{ "data": ... }` success bodies.
- Backend: Python 3.11+, PostgreSQL, separate worker, Redis queue for local development (or a durable documented equivalent), local filesystem object storage. GCP adapters: Document AI, Vertex AI Gemini, GCS, Pub/Sub, Cloud SQL. BigQuery/Firestore are optional, not prerequisite stores.
- Rate extraction and witness/re-look logic: use the root spec as the algorithm reference. Use decimal strings in JSON and Decimal internally, not its older floating-point persistence examples.
- Canonical boxes: `{x,y,width,height,coordinate_space:"normalized_page"}` against the actual preview image. Convert root-spec `[x0,y0,x1,y1]` and Gemini `[ymin,xmin,ymax,xmax]` at adapter boundaries.
- Access: public, no auth, accounts, roles, or tenancy. Keep secrets server-side, rate-limit, and enforce resource limits.
- No `/extract`, `/review/{record_id}`, or Streamlit endpoints: implement the `/api/v1` contracts below.
- Never infer an institution as verified evidence from a filename. If used as a hint, flag it and require review.
- Financial rules are conditional on the product model. A physics failure requires investigation; declaring a source inconsistency requires convergent actual source evidence. Do not “repair” a printed value just to make a formula pass.

## Frontend commands and connection

```sh
npm ci
npm run dev
```

The UI runs at `http://localhost:3000`. It initially opens a clearly labeled sample workspace. In **Settings**, enter `http://localhost:8000/api/v1` and click **Test & connect**. It checks `GET /documents` before switching. Alternatively set `NEXT_PUBLIC_API_BASE_URL` at frontend build time. No API key goes into the frontend.

Allow CORS from `http://localhost:3000` and `http://127.0.0.1:3000` in local development. Production origins must be configured explicitly. Browser requests are direct to FastAPI, so HTTPS frontend needs an HTTPS API.

Current frontend behavior:

- Lists up to 200 documents; queries results and traces for completed/review-required jobs.
- Polls in-flight job status approximately every 2.5 seconds; progress is shown only if non-null.
- Upload uses multipart `file`, then creates a job separately.
- Source preview is an image from the API; bounding boxes overlay its natural aspect ratio.
- Review is field-based, with optimistic result-version checking and a required reason.
- Live exports are created server-side; queued exports have a manual **Check status** action, then download when ready.
- Live export history is session-local currently. Backend history listing can be added later.
- Evaluation runs are polled by report ID.
- Schema versions are read-only in the current P0 UI. Publish the seed schema server-side.
- There is no silent demo fallback in live mode.

## Authoritative wire types

`lib/types.ts` is the source of truth for the UI. Implement equivalent Pydantic response DTOs. Internal database models can differ.

All reads and mutations return:

```json
{"data": {}, "meta": {"request_id": "uuid"}}
```

List endpoints put arrays directly in `data`, not nested `items`. Errors:

```json
{"error":{"code":"MANDATORY_VALIDATION_FAILED","message":"The corrected value fails APY validation.","details":[],"request_id":"uuid"}}
```

Externally exposed IDs are UUID strings; demo IDs beginning with `demo-` exist only in the browser sample state. UTC timestamps use ISO 8601. Rates, money, and confidence cross JSON as strings or null. Confidence must remain null until calculated from an explicit policy.

`DocumentItem` required fields:

```json
{
  "id":"uuid", "original_filename":"rates.pdf", "mime_type":"application/pdf",
  "size_bytes":12345, "page_count":1, "institution_name":"Example Bank",
  "created_at":"2026-10-20T05:00:00Z", "job_id":"uuid",
  "status":"review_required", "record_count":5, "result_version":1, "is_demo":false
}
```

For a just-uploaded document without a job, use `job_id:""`, `status:"queued"`, `record_count:0`, `result_version:0`, and institution `""`. The UI offers job creation if the initial create-job call failed. The document list projects the latest run.

Job statuses: `queued`, `preprocessing`, `parsing`, `extracting`, `validating`, `repairing`, `review_required`, `completed`, `failed`, `cancelled`.

`RateRecord` contains:

- `id`, `document_id`, `job_id`, `source_file`, `page` (1-based), `version` (job result version).
- `values`: exactly `institution_name`, `product_type`, `term_months`, `rate_pct`, `apy_pct`, `min_deposit`, `effective_date`, each a string or null.
- `fields`: an entry for every one of those keys, even when missing.
- Each field: `id`, `field_name`, `raw_value`, `normalized_value`, `review_status`, `evidence`, `witness_a`, `witness_b`.
- Field review states: `pending`, `accepted`, `corrected`, `rejected`, `deferred`.
- Witness values are real raw readings or null. Never label a second call to the same model as an independent OCR witness.
- Evidence: `id`, `document_id`, `page_number`, `bounding_box`, `source_text`, `source_type` (`native_text` or `ocr` for live documents). The frontend-only reconstruction type must not be returned for real sources.
- `status`: `validated`, `needs_review`, `human_verified`, or `rejected`.
- `validations`: `{rule_id,severity:"error"|"warning"|"info",status:"pass"|"fail",message,field_names:[]}`.
- `extraction_confidence`: string in 0..1 or null; not the organizer CSV score.

No accepted record may hide a rejected/deferred/pending required field or a blocking rule failure.

## Endpoint implementation checklist

All paths below are relative to `/api/v1`.

| Method / path | Request | `data` response |
|---|---|---|
| `GET /documents?limit=200` | limit | `DocumentItem[]`, latest run summary per document |
| `POST /documents` | multipart `file` | `DocumentItem`; HTTP 201 |
| `GET /documents/{id}` | — | `DocumentItem` |
| `GET /documents/{id}/pages` | — | page metadata |
| `GET /documents/{id}/pages/{n}/preview` | 1-based page | **binary PNG/JPEG**, correct content type, no JSON wrapper |
| `GET /schemas` | — | published `SchemaVersion[]` with fields inline |
| `POST /documents/{id}/jobs` | schema ID, options, idempotency key | `{job_id,status,status_url}`; HTTP 202 |
| `GET /jobs/{id}` | — | `Job` |
| `GET /jobs/{id}/results` | — | `{records:RateRecord[],result_version:number}` |
| `GET /jobs/{id}/validation` | — | persisted validations |
| `GET /jobs/{id}/trace` | — | `TraceEvent[]` chronological |
| `POST /jobs/{id}/cancel` | `{}` | updated `Job`; cooperate at stage boundaries |
| `POST /jobs/{id}/retry` | `{}` | **new run** `Job`, including its new `id` |
| `GET /jobs/{id}/review-queue` | — | unresolved field/record summaries |
| `POST /fields/{id}/review` | `ReviewInput` | full updated `{records,result_version}` for the job |
| `GET /fields/{id}/history` | — | `ReviewHistory[]` chronological |
| `GET /fields/{id}/evidence` | — | evidence DTOs |
| `GET /evidence/{id}` | — | evidence DTO |
| `POST /jobs/{id}/exports` | format, result_version, include_evidence_manifest | `ExportItem`; 201 ready or 202 queued |
| `GET /exports/{id}` | — | `ExportItem` |
| `GET /exports/{id}/download` | — | **binary CSV/JSON**, attachment filename, no wrapper |
| `POST /evaluations` | dataset/configurations | `EvaluationReport` with queued/running state |
| `GET /evaluations/{id}/report` | — | `EvaluationReport`, including status while in progress |
| `GET /healthz` | — | readiness/health; outside /api/v1 is also fine |

Job creation payload:

```json
{
  "schema_version_id":"uuid",
  "options":{"enable_repair":true,"require_evidence":true},
  "idempotency_key":"uuid"
}
```

`Job`: `id`, `document_id`, `status`, `stage`, `progress` (0..1 fraction or null, derived from completed work), `attempt_count`, `error_code` (null if none), optional safe `error_message`, `trace_id`, `result_version`.

`TraceEvent`: `id`, `job_id`, `stage`, `summary`, `status` (`success|warning|error`), `created_at`, `duration_ms` (number or null). Store richer provider usage/action artifacts internally. Surface actual actions, not hidden chain-of-thought.

Review payload:

```json
{
  "decision":"correct",
  "corrected_value":"1.080",
  "reason_code":"SOURCE_CORRECTION",
  "comment":"The original cell contains 1.080%.",
  "reviewer_name":"optional, unverified",
  "evidence_ids":["uuid"],
  "expected_result_version":1
}
```

Decisions: `accept|correct|reject|defer`. Reject stale versions with HTTP 409. Reject invalid correction/acceptance with 422. Validate evidence ownership and actual source support. The browser's rule checks are convenience only; the server is authoritative. Preserve `raw_value`, original evidence, and all previous normalized values. A correction appends a new complete job snapshot, reruns all dependent rules, and invalidates affected old approvals. Unchanged records are also included in the new snapshot. `ReviewHistory` includes all payload fields plus `id`, `field_id`, `previous_value`, `created_at`, `result_version`.

`ExportItem`: `id`, `job_id`, `document_name`, `result_version`, `format` (`csv|json`), `status` (`queued|processing|ready|failed`), `created_at`, optional `error_code`.

Export all rows of the specified snapshot with explicit review status; an export is not an approval. Rejected records must remain marked as requiring attention. Financial decimals stay strings. CSV begins with the organizer columns:

```text
source_file,institution_name,product_type,term_months,rate_pct,apy_pct,min_deposit,effective_date,extraction_confidence,needs_review
```

Append `decision,record_id,result_version`; optionally append documented provenance columns. Blank is null, UTF-8, CRLF rows, stable columns, proper quote escaping. Neutralize spreadsheet formulas in arbitrary text. JSON includes all fields/evidence/validations and result version. Ensure artifact exists before `ready`.

`SchemaVersion`: `{id,name,version,status:"published"|"draft",fields:[{name,type,required,description}]}`. Seed the schema in `lib/demo.ts` using a real UUID. Rate/APY are individually nullable, but at least one is required. Non-term products use `"0"` months. Dates use `YYYY-MM-DD`.

Evaluation POST:

```json
{"dataset_version":"ps06-synthetic-v1","configurations":["native","single_pass","evidence_first"]}
```

`EvaluationReport`:

```json
{
  "id":"uuid", "status":"completed", "dataset_version":"ps06-synthetic-v1",
  "pipeline_version":"git-sha", "document_count":0, "field_count":0, "exclusions":0,
  "metrics":[{"name":"field_accuracy","numerator":0,"denominator":0,"value":null}],
  "pipelines":[
    {"name":"native","field_accuracy":null,"evidence_coverage":null,"false_auto_accept_rate":null,"p95_latency_ms":null},
    {"name":"single_pass","field_accuracy":null,"evidence_coverage":null,"false_auto_accept_rate":null,"p95_latency_ms":null},
    {"name":"evidence_first","field_accuracy":null,"evidence_coverage":null,"false_auto_accept_rate":null,"p95_latency_ms":null}
  ],
  "estimated_cost_usd":null
}
```

This example defines shape, not sample measurements. Populate counts and metrics from actual runs only. All fractional metrics are 0..1; unknowns are null. Persist denominators/configuration/source hashes and per-field differences in the full report.

## Suggested backend layout

```text
backend/
  pyproject.toml
  app/
    main.py, config.py, contracts.py
    api/                 # documents, schemas, jobs, fields, exports, evaluation
    domain/              # state transitions, normalizers, validators, review policies
    pipeline/            # triage, witness_a, witness_b, reconcile, relook, scoring
    adapters/            # storage, queue, document_ai, gemini
    persistence/         # SQLAlchemy models, repositories, migrations
    worker.py
  tests/
  bench/                 # generator, manifest, truth, evaluator
  Dockerfile
compose.yaml             # web + API + worker + PostgreSQL + Redis
```

Own backend files, compose/infra, and backend documentation. Coordinate changes to `lib/types.ts` and `lib/api.ts`; do not change components or styling unnecessarily.

## Pipeline and domain acceptance

1. Validate signatures, declared type, parser compatibility, maximum 20 MB, configurable pages/pixels/timeout; reject path traversal and malformed input before provider calls.
2. Persist original by generated key/hash, metadata, request ID, job and queue delivery durably. Enforce idempotency and retry recovery, use transactions and leases. Duplicate messages must not duplicate results.
3. Render canonical pages. Native PDF words are witness A where available; use Document AI for scans. Gemini reads independent page content as witness B. Preserve layout structure, cell/header/footnote relationships and raw provider artifacts.
4. Validate model schema; no hallucinated missing values or invented evidence IDs. Use canonical coordinates tested for rotation, landscape, crops, and scaling.
5. Normalize deterministically using Decimal; keep raw strings. Rate 4.25 means 4.25%, not 0.0425 in the external contract.
6. Implement the root spec's rules: ranges, date format/plausibility, required fields, product enum, term/product consistency, duplicates/cross-page dates; apply rate/APY comparison only under documented compounding assumptions.
7. Known fixture: rate `1.076`, APY `1.162` fails the upper-bound check; `3.998`, `4.057` passes that check. Tolerances are percentage-point tolerances as documented. Validate all sample claims by running code.
8. Repair is targeted, allowlisted, bounded to two attempts per field/failure group, one page reprocess, three major recoveries per job, plus configured provider-call/time/cost budgets. Avoid nested retries multiplying calls. Record the trigger, action, new artifacts, outcome, latency and usage.
9. Source disagreement and source inconsistency are different states/reasons. Never silently rewrite source truth.
10. Required evidence, schema/type checks and all blocking validations must pass before auto-acceptance. A confidence score never overrides the gate.
11. A human decision does not bypass hard validation. Corrected records get new versions with complete history. Export old versions after newer reviews without changes.
12. Do not claim completion while required fields remain unresolved. Expose failure/cancellation and concise safe error codes.

## Fixtures and benchmark

`ps06_sample_rate_sheets.csv` contains exactly **150 rows grouped into 30 source filenames**. The referenced PDFs were not supplied. Frontend previews are reconstructed from CSV with demo labels; they are not OCR evidence.

Generate actual PDFs/images from the sample CSV with ReportLab/Pillow and store exact printed ground truth and geometry. Use deterministic template selection (stable hash, not Python randomized `hash`). Do not lose each row's effective date: groups may contain different dates. Generate scan/photo degradations and a deterministic repair fixture. Keep development and held-out document groups separate.

Run native baseline, LLM single-pass, then evidence + validation + bounded repair on the same corpus. Record labels, numerator/denominator, excluded fields, false acceptance, evidence correctness/coverage, repair success, review rate, latency and measured usage. Do not use the CSV's confidence/review flags as correctness labels. A reconstructed synthetic benchmark must be described as synthetic.

## Efficient build order

1. Contracts, migrations, seed schema, storage and durable jobs; fake-provider integration test.
2. Real PDF parsing/previews and source geometry; a live vertical slice with native text and Gemini.
3. Rule engine, review transaction/versioning, CSV/JSON snapshots.
4. OCR adapter and bounded repair with trace persistence.
5. Benchmark generator/runner and evaluation report endpoint.
6. Compose and GCP deployment instructions, smoke tests.

Prefer the working end-to-end slice before extra infrastructure. Do not provision paid services without project configuration and the user's authorization. Do not select an unverified “latest” model name: make model/region explicit configuration.

## Verification before handoff

Run and report actual results:

- Backend unit tests: parsing/normalization, physics tolerance, dates, state transitions, acceptance gates, retry budgets, coordinate conversion, CSV safety.
- API integration: upload, signature mismatch, limits, missing schema, duplicate idempotency key, queue redelivery, provider failure, evidence ownership.
- Review: invalid correction 422, hard-failure accept 422, stale version 409, raw source immutability, dependent revalidation, concurrent reviewers.
- Export: immutable old-version output, unavailable version, absent artifact, formula-safe CSV, decimal preservation.
- Frontend `npm run typecheck`, `npm test`, `npm run build`, `npm run test:e2e`.
- Add live integration browser tests using a test backend. Existing demo tests and mocked API tests are not proof of provider integration.
- Launch local stack, connect through Settings, upload a real PDF and image, inspect actual highlights, correct/review, export, run the benchmark.

Deliver a clear list of implemented functionality, test results, required environment variables, startup commands, deployment assumptions, and unfinished work. Never substitute invented metrics for a missing benchmark.
