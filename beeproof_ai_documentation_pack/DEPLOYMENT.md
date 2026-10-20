# Development and GCP Deployment Guide

## 1. Prerequisites

Local: - Git; - Docker and Docker Compose; - Python version selected by
the project; - Node.js version selected by the project; - access to a
supported terminal.

Cloud: - GCP project with billing; - permission to enable required APIs
and create resources; - Document AI processor configured; - Vertex AI
access; - Terraform; - GitHub repository and approved CI/CD identity.

Pin supported versions in repository configuration rather than relying
on whatever happens to be installed.

## 2. Local setup

1.  Clone the repository.
2.  Copy `.env.example` to `.env`.
3.  Set required local database, queue, storage, and provider settings.
4.  Start services using Docker Compose.
5.  Apply database migrations.
6.  Load synthetic fixtures.
7.  Run unit and integration tests.
8.  Open the web application and process a sample document.

The exact commands must be finalized to match the implementation. Keep a
single documented happy path, such as `make dev`, `make test`, and
`make lint`.

## 3. Local environment variables

Document variables such as: - `APP_ENV` - `API_BASE_URL` -
`DATABASE_URL` - `QUEUE_URL` - `OBJECT_STORAGE_ENDPOINT` -
`OBJECT_STORAGE_BUCKET` - `OBJECT_STORAGE_ACCESS_KEY` -
`OBJECT_STORAGE_SECRET_KEY` - `GOOGLE_CLOUD_PROJECT` -
`GOOGLE_CLOUD_LOCATION` - `DOCUMENT_AI_PROCESSOR_ID` -
`VERTEX_AI_MODEL` - `MAX_UPLOAD_BYTES` - `MAX_DOCUMENT_PAGES` -
`MAX_REPAIR_ATTEMPTS` - `MAX_JOB_COST_USD` - `LOG_LEVEL`

Only include variables actually consumed by the implementation.
`.env.example` must contain placeholders, never live credentials.

## 4. GCP setup

1.  Choose a project and region.
2.  Confirm service and model availability.
3.  Enable required APIs.
4.  Configure budgets and alerts.
5.  Create runtime service accounts with least privilege.
6.  Create Artifact Registry.
7.  Create Cloud SQL PostgreSQL.
8.  Create Cloud Storage buckets.
9.  Create Pub/Sub topic/subscription.
10. Configure Document AI and Vertex AI.
11. Configure Secret Manager.
12. Provision resources using Terraform.
13. Configure CI/CD identity and permissions.

## 5. Container deployment

Build separate images for API and worker if their dependencies and
runtime needs differ. Use non-root users where practical, minimal base
images, pinned dependencies, and health checks.

Cloud Run API: - handle HTTP requests; - validate requests; -
create jobs; - read results; - initiate exports and review actions.

Cloud Run worker: - consume or receive queue-triggered work; - process
jobs; - persist stage results; - acknowledge only after durable success
or controlled terminal handling.

Use a queue integration pattern compatible with Cloud Run and the
selected Pub/Sub delivery mode. Validate retry semantics carefully.

Deploy the API and web services with `--allow-unauthenticated`. The
application has no login or authorization layer.

## 6. Database connectivity

-   Use secure Cloud SQL connectivity.
-   Keep connection pool sizes compatible with Cloud Run concurrency and
    Cloud SQL limits.
-   Apply migrations as a controlled release step.
-   Avoid running competing migrations from every application instance.
-   Test backup and restore.

## 7. CI/CD pipeline

Recommended sequence: 1. Checkout. 2. Install locked dependencies. 3.
Format/lint checks. 4. Static type checks. 5. Unit tests. 6. Integration
tests using services/containers. 7. Dependency and image scan. 8. Build
images. 9. Push to Artifact Registry. 10. Deploy to a staging/review
environment. 11. Smoke tests. 12. Promote to production with explicit
approval where appropriate.

Use workload identity federation where supported instead of long-lived
service-account keys.

## 8. Observability

Log structured fields: - timestamp; - severity; - service; - request
ID; - trace ID; - document/job ID; - stage; - error code; - duration; -
provider identifier.

Do not log secrets or entire documents by default. Set alerts for queue
backlog, repeated failures, provider quota errors, latency spikes, and
cost anomalies.

## 9. Cost management

-   Set budgets and alerts.
-   Track pages and provider calls.
-   Avoid duplicate parsing.
-   Cache immutable layout artifacts.
-   Enforce global repair budgets.
-   Limit concurrency.
-   Use provider pricing and actual usage metadata to estimate costs.
-   Mark cost as unknown when reliable usage/pricing data is
    unavailable.

## 10. Release checklist

-   [ ] Required APIs enabled.
-   [ ] API and web services reachable without login.
-   [ ] IAM least privilege reviewed.
-   [ ] Secrets configured.
-   [ ] Database migrations applied.
-   [ ] Storage access restricted.
-   [ ] Queue delivery verified.
-   [ ] Parser and LLM provider calls verified.
-   [ ] Health checks pass.
-   [ ] Smoke tests pass.
-   [ ] Logs contain trace IDs.
-   [ ] Budget alerts enabled.
-   [ ] Rollback procedure documented.
-   [ ] Sample document processed successfully.
-   [ ] Review and export verified.
