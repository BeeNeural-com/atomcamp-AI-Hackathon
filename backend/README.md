# BeeProof AI — Backend

FastAPI backend for PS-06: turns bank rate sheets (PDF, scans, photos) into validated,
audit-ready records. Every value carries its source region, the readings of two
independent witnesses, the rules it passed or failed, and its review history.

- Pipeline: root [`ARCHITECTURE.md`](../ARCHITECTURE.md) §5–§12 (two witnesses, validators, bounded re-look, scoring).
- API, data model, states: [`beeproof_ai_documentation_pack/`](../beeproof_ai_documentation_pack/) (`/api/v1`, open access per ADR-004).
- Machine-readable contract: [`openapi.json`](openapi.json) (also live at `/openapi.json`, docs at `/docs`).

## Run everything with one command (Docker)

From the repository root:

```bash
docker compose up --build
```

| Service | URL / port | What it is |
|---|---|---|
| frontend | http://localhost:3000 | Next.js UI, built with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1` |
| api | http://localhost:8000/docs | FastAPI. Creates tables on first start; `WORKER_MODE=off` |
| worker | — | `python -m beeproof.worker`; scale with `--scale worker=3` |
| postgres | localhost:5432 | PostgreSQL 16 (`beeproof` / `beeproof` / `beeproof`), volume `pgdata` |
| redis | localhost:6379 | Redis 7 with AOF persistence: job queue and shared rate-limit counters |

Startup is health-gated: postgres + redis, then api, then worker + frontend.
- `docker compose logs -f api worker` follows the backend logs.
- `docker compose down` stops everything; `docker compose down -v` also deletes all data.
- Ports can be overridden with `API_PORT`, `FRONTEND_PORT`, `POSTGRES_PORT` and `REDIS_PORT`. If you change
  the API port, also set `PUBLIC_API_URL` (it is baked into the frontend build).

**How Redis is used:** the job row in Postgres is the source of truth. After the API commits a new job, it
`LPUSH`es the job id to `beeproof:jobs`. Workers block on `BRPOP` and wake immediately, then claim the job
with an atomic conditional `UPDATE`. A duplicate message finds the job already claimed. A lost message (Redis
restart) is picked up by the workers' database sweep, which runs every 5 seconds. Rate limits are counted in
Redis, so they apply across API replicas.

**GCP inside Docker:** put `EXTRACTOR=gemini`, `PROJECT`, `GEMINI_MODEL`, `DOCAI_PROCESSOR_ID`, etc. in
`backend/.env`. Compose loads it into api and worker; compose's own settings such as `DATABASE_URL` and
`QUEUE_URL` take precedence. Run `gcloud auth application-default login` on the host first. Your gcloud
folder (`%APPDATA%\gcloud` on Windows, `~/.config/gcloud` elsewhere, or `GCLOUD_CONFIG_DIR`) is mounted
read-only at `/gcp`.

## Quick start without Docker (no cloud needed)

```bash
cd backend
uv venv --python 3.12 .venv            # or: python -m venv .venv
uv pip install --python .venv -e ".[dev,gcp]"
.venv/Scripts/python -m uvicorn beeproof.api.app:app --reload --port 8000   # Windows
# .venv/bin/python -m uvicorn ...                                            # macOS/Linux
```

Open http://localhost:8000/docs. SQLite and local file storage are created under `backend/data/`.
The job worker runs inside the API process (`WORKER_MODE=inline`).

```bash
.venv/Scripts/python -m pytest              # 99 tests, no network
.venv/Scripts/python -m bench.generate      # synthetic rate sheets + ground truth -> data/bench
.venv/Scripts/python -m bench.evaluate      # ablation report -> data/bench/report.md
```

For the full stack (frontend, API, worker, Postgres, Redis), see the Docker section above.

### Extractors

| `EXTRACTOR` | What it reads | Needs |
|---|---|---|
| `heuristic` (default) | Digital PDFs via the text layer. Returns no rows for scans/photos. | nothing |
| `gemini` | Any page image (Vertex AI vision, structured output) | `PROJECT`, `GEMINI_MODEL`, ADC credentials |

Witness A is PyMuPDF words for digital pages, and Document AI Form Parser for scans when
`DOCAI_PROCESSOR_ID` is set. Without Document AI, images have a single witness and every row goes to review.
See [`.env.example`](.env.example) for all settings.

## Frontend integration guide

**Wire types match the frontend's [`lib/types.ts`](../lib/types.ts)** (per `CLAUDE_BACKEND_HANDOFF.md`).
This is checked by compiling live API responses against those types with `tsc --strict`. Extra provenance
keys (`decision`, `review_reasons`, `confidence`, `agreement`, `relook_values`, …) are additive.

All JSON responses: `{"data": ..., "meta": {"request_id", "next_cursor"}}`.
All errors: `{"error": {"code", "message", "details": [{field, reason}], "request_id"}}`.
No authentication — every endpoint is public (ADR-004). CORS defaults to `*` (`CORS_ORIGINS`).
Mutating requests are rate-limited per client IP (`RATE_LIMIT_PER_MIN`, default 120; returns `429 RATE_LIMITED`).

In the UI: **Settings → API base URL `http://localhost:8000/api/v1` → Test & connect**.

Contract behaviours worth knowing:
- `GET /schemas` returns published `SchemaVersion[]`. Its `id` is the `schema_version_id` for job creation.
- `POST /fields/{id}/review` takes `expected_result_version` (a stale one returns `409 STALE_RESULT_VERSION`)
  and returns the job's complete new `{records, result_version}`.
- Accepting a field that fails a blocking rule, or correcting it to a value that still fails one, returns
  `422 MANDATORY_VALIDATION_FAILED`. A `DOCUMENT_INCONSISTENCY` row can therefore only be corrected or rejected.
- A job completes when every review item has a decision. Rejected records stay `rejected` and export with
  `needs_review=True`.
- `POST /jobs/{id}/retry` (failed or cancelled jobs) creates a **new run** and returns that job.
- Fields that need no review report `review_status: "accepted"` with `review_required: false`.
- Evaluations accept `configurations: ["native","single_pass","evidence_first"]`. `single_pass` (an LLM
  baseline) is reported as skipped with null metrics unless `EXTRACTOR=gemini`.

### Main flow

```
POST /api/v1/documents            multipart "file"  (add ?process=true to also queue a job)
POST /api/v1/documents/{id}/jobs  {"options": {"enable_repair": true}, "idempotency_key": "..."}  -> 202 {job_id}
GET  /api/v1/jobs/{job_id}        poll every ~1s until status is review_required | completed | failed
GET  /api/v1/jobs/{job_id}/results        records + fields + evidence (latest result version)
GET  /api/v1/jobs/{job_id}/review-queue   open review items, lowest confidence first
POST /api/v1/fields/{field_id}/review     {"decision": "accept|correct|reject|defer", "corrected_value": "1.081%"}
POST /api/v1/jobs/{job_id}/exports        {"format": "csv|json", "result_version": 3}  -> download_url
GET  /api/v1/jobs/{job_id}/trace          action timeline (stage, agent, reason, summary, artifact_urls)
```

Job `status` values: `queued → preprocessing → parsing → extracting → validating ⇄ repairing →
review_required | completed`, plus `failed` and `cancelled`. `stage` mirrors the current step.
No percentage is reported because progress isn't measurable.

### Results shape (abridged)

```jsonc
{
  "result_version": 1,
  "summary": {"records": 5, "auto_approved": 2, "needs_review": 0, "document_inconsistency": 3, "open_review_items": 6},
  "records": [{
    "id": "uuid", "record_key": "p1r0", "page_number": 1, "product_label": "Money Market",
    "decision": "DOCUMENT_INCONSISTENCY",        // AUTO_APPROVED | NEEDS_REVIEW | DOCUMENT_INCONSISTENCY
    "status": "document_inconsistency",          // ... | auto_approved | human_verified | rejected
    "needs_review": true, "extraction_confidence": 0.75,
    "review_reasons": ["APY_PHYSICS_MAX: printed APY 1.162% exceeds max possible 1.082% for rate 1.076%"],
    "values": {"rate_pct": "1.076", "apy_pct": "1.162", "term_months": "0", "...": "..."},   // decimal strings
    "fields": [{
      "id": "uuid", "name": "apy_pct", "raw_value": "1.162%", "normalized_value": "1.162",
      "confidence": 0.75, "agreement": 1.0, "grounded": true,
      "witness_a": "1.162%", "witness_b": "1.162%", "relook_values": ["1.162%"],
      "validators": {"APY_PHYSICS_MAX": "fail", "RANGE_APY": "pass"},
      "review_status": "pending", "verification_status": "evidence_linked",
      "evidence": [{"page_number": 1, "preview_url": "/api/v1/documents/{doc}/pages/1/preview",
                    "bounding_box": {"x": 0.60, "y": 0.155, "width": 0.044, "height": 0.014,
                                     "coordinate_space": "normalized_page"}}]
    }]
  }]
}
```

**Drawing evidence boxes:** `bounding_box` is normalized 0–1 with the origin at top-left, relative to the
page preview PNG. Multiply by the rendered image's displayed width and height. The preview is already rotated
and deskewed, so no further transforms are needed.

**Review rules the UI should reflect:**
- A correction that breaks a field-level hard rule (range, date format) returns
  `422 REVIEW_DECISION_INVALID`. Show `error.details`.
- Every review creates a new `result_version`. Older versions stay downloadable.
- The job moves to `completed` when no field is `pending` or `deferred` and no record is flagged.
- `DOCUMENT_INCONSISTENCY` means both witnesses and the re-look read the same value, and the value breaks a
  hard rule. The source document itself is wrong. Accepting the fields confirms that.

Re-look crops (zoomed cell images) are listed in `trace[].artifact_urls`. Each one is a PNG.

## Evaluation API

`POST /api/v1/evaluations {"configs": ["extractor_only","full"], "limit": 20}` runs the ablation in
the background. Poll `GET /api/v1/evaluations/{id}`; `metrics` holds per-config results, each as
`{value, num, den}`. `GET /api/v1/evaluations/{id}/report?format=md` returns the report.

## Deploy to Cloud Run

```bash
gcloud run deploy beeproof-api --source backend --region $REGION --allow-unauthenticated \
  --memory 2Gi --cpu 2 --no-cpu-throttling --max-instances 1 \
  --set-env-vars STORE=gcs,BUCKET=$BUCKET,PROJECT=$PROJECT,EXTRACTOR=gemini,GEMINI_MODEL=$GEMINI_MODEL,DATABASE_URL=$DATABASE_URL
```

`--no-cpu-throttling` keeps the inline worker running between requests. `--max-instances 1` is
required with SQLite. Use Cloud SQL (`DATABASE_URL=postgresql+psycopg://...`) to scale beyond one instance,
and run `python -m beeproof.worker` as a separate service.

## Decisions and deviations from the docs

- **Queue:** Redis delivers job ids (`QUEUE_URL`), and Postgres holds the durable job state. Workers claim jobs
  with an atomic conditional `UPDATE`, so a duplicate claim is impossible. A stale-heartbeat sweep re-queues jobs
  whose worker died. Without `QUEUE_URL` (local dev, tests) workers poll the database instead. Pub/Sub is not
  implemented yet.
- **Storage:** SQLite or PostgreSQL through SQLAlchemy, plus local disk or GCS for files. BigQuery and Firestore
  (root spec §9) are not used. The pack's Postgres model replaces them.
- **No migrations tool yet.** Tables are created at startup. Add Alembic before any schema change on shared data.
  After pulling model changes, delete `backend/data/*.db` locally.
- **Decimals:** values cross JSON and are stored as decimal strings. Rule arithmetic uses floats with the
  documented percentage-point tolerances (the handoff asks for `Decimal` internally; not done yet).
- **Benchmark generator** groups rows by `(source_file, effective_date)` and drops 16 duplicate rows (see
  `bench/generate.py`). In the sample, 28 of 30 files mix effective dates.
- **CD rows without a term** (7 in the sample) fail `TERM_PRODUCT_CONSISTENT` instead of passing as
  "n/a".
- **Review-rate target:** measured excluding document-inconsistent rows. About 60% of sample rows break APY
  physics by construction.

## Known limitations

- The `heuristic` extractor only reads digital PDFs. Scans and photos need `EXTRACTOR=gemini` (and
  Document AI for a second witness). The Gemini and Document AI adapters have not been run against live
  services in this repo yet.
- On digital PDFs with the heuristic extractor, Witness A and Witness B both come from the same text layer.
  Their agreement is not independent evidence (the report says so).
- Benchmark numbers are synthetic. They are not a claim about real-world accuracy.
- Open access: anyone with the URL can upload, review and export. Use synthetic or public documents only.
