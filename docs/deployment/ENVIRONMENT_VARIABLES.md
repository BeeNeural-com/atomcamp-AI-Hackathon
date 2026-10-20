# Environment variable inventory

Classes:
- **Public**: may appear in the browser bundle.
- **Internal**: non-sensitive service configuration.
- **Secret**: stored in Secret Manager or as a GitHub secret.
- **Generated**: produced by Terraform or Cloud Run.

## Frontend (`beeproof-web`)

| Variable | Class | Production value | Notes |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Public | `https://proof.beeneural.com/api/v1` | Build argument; baked into the bundle by the deploy workflow |
| `PORT` | Generated | set by Cloud Run (3000) | |

No secrets ever reach the frontend build.

## API (`beeproof-api`) and worker (`beeproof-worker`)

Set by Terraform (`infra/terraform/cloudrun.tf`).

| Variable | Class | Value / source | Service |
|---|---|---|---|
| `DATABASE_URL` | Secret | Secret Manager `beeproof-database-url` (runtime role `beeproof_app`, private IP, `sslmode=require`) | api, worker |
| `APP_ENV` | Internal | `production` | both |
| `LOG_FORMAT` | Internal | `json` | both |
| `STORE` / `BUCKET` (alias `GCS_BUCKET`) | Internal / Generated | `gcs` / `absolute-access-415010-beeproof-documents` | both |
| `PROJECT` (alias `GCP_PROJECT_ID`) | Internal | `absolute-access-415010` | both |
| `AUTO_CREATE_TABLES` | Internal | `false`: schema is owned by migrations | both |
| `WORKER_MODE` | Internal | `off` | both |
| `CLOUD_TASKS_QUEUE` | Generated | `projects/absolute-access-415010/locations/asia-south1/queues/beeproof-jobs` | both |
| `WORKER_SERVICE_URL` | Generated | `https://beeproof-worker-<project-number>.asia-south1.run.app` | both |
| `TASKS_INVOKER_SERVICE_ACCOUNT` | Generated | `beeproof-tasks-invoker@…` | both |
| `CORS_ORIGINS` | Internal | `https://proof.beeneural.com` | both |
| `MAX_UPLOAD_BYTES` | Internal | `20971520` | both |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | Internal | api 2/2, worker 2/1 | both |
| `RATE_LIMIT_PER_MIN` | Internal | `120` | api |
| `EXTRACTOR` | Internal | `gemini` if `gemini_model` is set, else `heuristic` | worker |
| `GEMINI_MODEL` | Internal | tfvars `gemini_model` (exact Model Garden ID) | worker |
| `GEMINI_LOCATION` (alias `VERTEX_AI_LOCATION`) | Internal | tfvars, default `asia-south1` | worker |
| `LAYOUT_PROVIDER` / `DOCAI_LOCATION` / `DOCAI_PROCESSOR_ID` | Internal | `auto` / `us` / tfvars | worker |
| `PAGE_CONCURRENCY` | Internal | `4` | worker |
| `K_REVISION` | Generated | set by Cloud Run; reported by `/api/v1/health` | both |

Vertex AI and Document AI authenticate with the worker's service account (no API keys).

## Migration job (`beeproof-migrate`)

| Variable | Class | Source |
|---|---|---|
| `DATABASE_URL` | Secret | Secret Manager `beeproof-migrator-database-url` (schema owner `beeproof_migrator`) |
| `DB_APP_USER` | Internal | `beeproof_app` (receives data-only grants) |
| `LOG_FORMAT` | Internal | `json` |

## Connector VM

| Value | Class | Source |
|---|---|---|
| Tunnel token | Secret | Secret Manager `beeproof-cloudflare-tunnel-token`, read at boot |

## GitHub Actions

| Name | Kind | Class |
|---|---|---|
| `GCP_PROJECT_ID`, `GCP_REGION` | variable | Internal |
| `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT` | variable | Generated (Terraform output) |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_TUNNEL_ID`, `PUBLIC_SMOKE_TEST` | variable | Internal |
| `CLOUDFLARE_TUNNEL_TOKEN`, `CLOUDFLARE_API_TOKEN` | secret | Secret |

## Local development

See `backend/.env.example` and the root `.env.example`, which contain placeholders only. docker compose sets
`DATABASE_URL`, `QUEUE_URL` (Redis) and `AUTO_CREATE_TABLES=false` itself.
