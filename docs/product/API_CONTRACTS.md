# API Contracts

## 1. Conventions

Base path: `/api/v1`.

-   JSON request and response bodies unless explicitly documented.
-   UTC ISO-8601 timestamps.
-   UUID identifiers.
-   Pagination uses `limit` and `cursor` or a consistent offset
    strategy.
-   The API is open: no authentication or authorization. Every
    endpoint is public.
-   Long-running operations return a job/resource identifier.
-   Use request IDs and trace IDs for troubleshooting.
-   Do not return stack traces or secrets to clients.

## 2. Standard response shapes

### Success

``` json
{
  "data": {},
  "meta": {
    "request_id": "uuid"
  }
}
```

### Error

``` json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request did not satisfy the required schema.",
    "details": [
      {
        "field": "schema.fields[0].type",
        "reason": "Unsupported data type"
      }
    ],
    "request_id": "uuid"
  }
}
```

The exact wrapper may vary if an existing repository already has a
stable convention; document and apply one convention consistently.

## 3. Endpoint inventory

### Documents

-   `POST /documents` --- upload a document.
-   `GET /documents` --- list documents.
-   `GET /documents/{document_id}` --- retrieve metadata.
-   `GET /documents/{document_id}/pages` --- page metadata and layout
    summary.
-   `GET /documents/{document_id}/pages/{page_number}/preview` ---
    page preview image.
-   `DELETE /documents/{document_id}` --- initiate deletion according to
    retention policy.

Upload may use multipart form data or a two-step signed-upload flow.
Choose one and document maximum size and allowed types.

### Schemas

-   `GET /schemas`
-   `POST /schemas`
-   `GET /schemas/{schema_id}`
-   `GET /schemas/{schema_id}/versions`
-   `POST /schemas/{schema_id}/versions`
-   `POST /schemas/{schema_id}/validate`

A published schema version is immutable.

### Jobs

-   `POST /documents/{document_id}/jobs`
-   `GET /jobs/{job_id}`
-   `GET /jobs/{job_id}/results`
-   `GET /jobs/{job_id}/validation`
-   `GET /jobs/{job_id}/trace`
-   `POST /jobs/{job_id}/retry`
-   `POST /jobs/{job_id}/cancel`

Job creation request example:

``` json
{
  "schema_version_id": "uuid",
  "options": {
    "enable_repair": true,
    "require_evidence": true
  },
  "idempotency_key": "client-generated-key"
}
```

Response example:

``` json
{
  "data": {
    "job_id": "uuid",
    "status": "queued",
    "status_url": "/api/v1/jobs/uuid"
  }
}
```

### Evidence

-   `GET /evidence/{evidence_id}`
-   `GET /fields/{field_id}/evidence`

Evidence endpoints must not expose raw storage paths.

### Review

-   `GET /jobs/{job_id}/review-queue`
-   `POST /fields/{field_id}/review`
-   `GET /fields/{field_id}/history`

Review request example:

``` json
{
  "decision": "correct",
  "corrected_value": "1255.00",
  "reason_code": "DECIMAL_CORRECTION",
  "comment": "The source cell contains 1,255.00.",
  "reviewer_name": "optional, unverified",
  "evidence_ids": ["uuid"]
}
```

The server must validate the corrected value and run applicable rules. A
reviewer action must not bypass hard safety constraints.

### Exports

-   `POST /jobs/{job_id}/exports`
-   `GET /exports/{export_id}`
-   `GET /exports/{export_id}/download`

Export creation request:

``` json
{
  "format": "csv",
  "result_version": 3,
  "include_evidence_manifest": true
}
```

Supported formats for P0: `csv`, `json`.

### Evaluation

-   `POST /evaluations`
-   `GET /evaluations/{evaluation_id}`
-   `GET /evaluations/{evaluation_id}/report`

## 4. Job response fields

Include: - `id` - `document_id` - `status` - `stage` - `progress` only
if calculated from actual completed work - `attempt_count` -
`created_at` - `started_at` - `completed_at` - `error_code` - `trace_id`

Do not invent a percentage when stage progress is not meaningfully
measurable.

## 5. HTTP status conventions

-   `200`: successful read/update.
-   `201`: resource created synchronously.
-   `202`: asynchronous operation accepted.
-   `400`: malformed request.
-   `404`: missing resource.
-   `409`: state conflict or idempotency conflict.
-   `413`: payload too large.
-   `415`: unsupported media type.
-   `422`: valid JSON but invalid domain/schema content.
-   `429`: rate or quota limit.
-   `500`: unexpected internal failure.
-   `502/503/504`: upstream/provider or availability failure where
    appropriate.

## 6. Error codes

Define stable machine-readable codes, including:

-   `INVALID_FILE`
-   `UNSUPPORTED_FILE_TYPE`
-   `FILE_TOO_LARGE`
-   `PAGE_LIMIT_EXCEEDED`
-   `SCHEMA_NOT_FOUND`
-   `SCHEMA_VERSION_IMMUTABLE`
-   `JOB_STATE_CONFLICT`
-   `PROVIDER_TIMEOUT`
-   `PROVIDER_QUOTA_EXCEEDED`
-   `PARSER_FAILED`
-   `EXTRACTION_OUTPUT_INVALID`
-   `EVIDENCE_NOT_FOUND`
-   `MANDATORY_VALIDATION_FAILED`
-   `REPAIR_BUDGET_EXHAUSTED`
-   `REVIEW_DECISION_INVALID`
-   `EXPORT_FAILED`

## 7. API versioning

Prefer additive changes within `/v1`. Breaking changes require a new API
version or an explicit migration plan. Generate OpenAPI from FastAPI
and, where practical, generate frontend types from the OpenAPI document.
