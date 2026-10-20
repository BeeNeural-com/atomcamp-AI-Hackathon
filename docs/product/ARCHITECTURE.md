# System Architecture

## 1. Architecture style

Use a modular monolith for the API/domain layer and a separate worker
process for long-running jobs. Keep domain logic independent from cloud
services. Avoid premature microservices.

The system is open: no authentication, authorization, accounts or
workspaces.

## 2. Logical components

### Web application

Owns presentation, user interaction, client-side validation, and polling
or event subscription for job progress. It does not call model providers
directly.

### API

Owns request validation, document/job lifecycle, review operations,
export initiation, and API contracts.

### Domain services

Own schema validation, normalization, state transitions, acceptance
rules, repair budgets, and audit invariants.

### Worker

Consumes jobs and runs the pipeline. It is independently scalable from
the API.

### Provider adapters

Wrap Document AI, Gemini/Vertex AI, object storage, queueing, and
optional local mocks behind stable interfaces.

### Persistence

PostgreSQL stores metadata and structured results. Object storage stores
originals, page previews, provider responses, and larger artifacts.

### Queue

Local development uses a Redis-backed queue or documented alternative.
Production uses Pub/Sub. Queue messages should contain identifiers, not
sensitive document contents.

## 3. Data flow

1.  Browser posts a file to the API.
2.  API validates the file and writes the original to object storage.
3.  API persists document metadata and creates an extraction job.
4.  Dispatcher publishes a job identifier.
5.  Worker claims the job using a transactional state change.
6.  Worker creates page/layout artifacts.
7.  Extraction adapter returns candidate field values and evidence
    references.
8.  Validation service persists rule results.
9.  Supervisor accepts, repairs, or escalates.
10. Final result version is committed.
11. UI reads job state and results from the API.
12. Export service generates a version-bound artifact.

## 4. Suggested deployment topology

### Local

-   `web`
-   `api`
-   `worker`
-   `postgres`
-   `redis` (if selected queue requires it)
-   `minio` (optional)
-   optional local mock providers

### GCP

-   Cloud Run API service
-   Cloud Run worker service
-   Cloud SQL for PostgreSQL
-   Cloud Storage buckets
-   Pub/Sub topic and subscription
-   Document AI
-   Vertex AI Gemini
-   Secret Manager
-   Artifact Registry
-   Cloud Logging and monitoring
-   Terraform-managed IAM and infrastructure

Verify the selected services and processor are available in the chosen
regions and that quotas are sufficient.

## 5. Module boundaries

-   `documents`: file validation, metadata, storage lifecycle.
-   `schemas`: schema definitions and versioning.
-   `jobs`: job creation, state transitions, retries.
-   `layout`: parser adapters and canonical layout graph.
-   `extraction`: schema-aware field proposals.
-   `evidence`: source references and coordinate mapping.
-   `validation`: deterministic rule engine.
-   `repair`: repair action registry and budgets.
-   `review`: human decisions and version history.
-   `exports`: CSV/JSON generation.
-   `evaluation`: datasets, ground truth, metrics, reports.
-   `observability`: logs, traces, metrics and cost accounting.

Modules must not directly manipulate another module's tables except
through a documented repository/service interface.

## 6. Provider adapter contracts

### Document parser

Input: stored document reference, processing options, page selection.\
Output: canonical pages, text spans, regions, tables, cells,
coordinates, provider metadata.

### LLM extraction

Input: schema, relevant canonical layout subset, evidence candidate IDs,
prompt/config version.\
Output: typed extraction proposal with candidate evidence references and
ambiguity markers.

### Object storage

Operations: put, get, head, delete.

### Queue

Operations: publish job identifier, acknowledge message, retry transient
failure, dead-letter terminal failure.

### Cost meter

Record model usage and provider billing estimates when available.
Unknown costs remain unknown rather than fabricated.

## 7. State and consistency

-   Use transactions for job state transitions.
-   Treat queue delivery as at least once.
-   Use idempotency keys for externally initiated operations where
    appropriate.
-   Persist stage outputs before advancing to dependent stages.
-   Store the pipeline configuration snapshot with each job.
-   A changed configuration creates a new run/version.
-   Exports reference an immutable result version.
-   Keep audit events append-only.

## 8. Failure handling

Transient provider/network errors may be retried with bounded
exponential backoff. Invalid files and other permanent errors
should fail without retry loops. When a worker dies, a lease/heartbeat
mechanism or stale-job recovery task should return eligible jobs to a
safe state.

## 9. Scalability

Scale API and worker independently. Limit worker concurrency according
to provider quotas, memory requirements, database connection capacity,
and budget. Use page-level parallelism only where ordering and table
continuation are preserved. Avoid unconstrained fan-out.

## 10. Architecture diagram

``` mermaid
flowchart TD
  U[User] --> W[Next.js Web]
  W --> A[FastAPI]
  A --> DB[(PostgreSQL)]
  A --> OS[(Object Storage)]
  A --> Q[Queue]
  Q --> WK[Processing Worker]
  WK --> PAR[Document AI Adapter]
  WK --> LLM[Gemini Adapter]
  WK --> VAL[Deterministic Validation]
  WK --> REP[Bounded Repair Planner]
  PAR --> ART[Canonical Layout Artifacts]
  LLM --> RES[Versioned Extraction Results]
  VAL --> RES
  REP --> WK
  RES --> DB
  ART --> OS
  DB --> W
  DB --> EX[Export Service]
  EX --> OS
```
