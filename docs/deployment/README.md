# BeeProof AI — Production deployment

Target: GCP project `absolute-access-415010`, region `asia-south1`.
Public URL: **https://proof.beeneural.com**, through Cloudflare Tunnel `beeproof` (`48f78845-58d9-4aa2-9411-0a2ee7c0aa74`).

## Architecture

```
Browser ──HTTPS──> Cloudflare edge (TLS, WAF) ──Tunnel──> cloudflared VM(s) (no public IP, outbound only)
                                                              │ http://10.10.0.10:80
                                                              ▼
                                       Regional internal Application Load Balancer
                                         /api, /api/*  → beeproof-api  (Cloud Run, ingress internal)
                                         everything else → beeproof-web (Cloud Run, ingress internal)
beeproof-api ──Cloud Tasks (OIDC, rate-limited)──> beeproof-worker (Cloud Run, internal, IAM-only)
Cloud Scheduler (*/5) ──OIDC──> beeproof-worker /internal/sweep (stale-job recovery, lost deliveries)
api / worker / migrate job ──Direct VPC egress──> Cloud SQL PostgreSQL 16 (private IP, TLS)
api / worker ──> Cloud Storage (private bucket)   worker ──> Vertex AI Gemini, Document AI
GitHub Actions ──OIDC / Workload Identity Federation──> Artifact Registry, Cloud Run (deploy only)
```

| Component | Resource | Notes |
|---|---|---|
| Frontend | Cloud Run `beeproof-web` | 1 vCPU / 1 GiB, concurrency 40, 0–10 instances |
| API | Cloud Run `beeproof-api` | 1 vCPU / 1 GiB, concurrency 20, 0–10 instances |
| Worker | Cloud Run `beeproof-worker` | 2 vCPU / 4 GiB, concurrency 2, 0–10 instances, 30-min timeout |
| Migrations | Cloud Run Job `beeproof-migrate` | Runs once per deploy, advisory-locked |
| Queue | Cloud Tasks `beeproof-jobs` | Max 4 concurrent / 2 per s, 5 attempts, exponential backoff |
| Database | Cloud SQL `beeproof-pg` (PostgreSQL 16) | Private IP only, backups + 7-day PITR, deletion protection |
| Files | GCS `absolute-access-415010-beeproof-documents` | Uniform access, public access prevention, versioned |
| Ingress | Internal ALB `10.10.0.10` + Cloudflare Tunnel connector VM | Single hostname, path routing |

## Documents

| Doc | Contents |
|---|---|
| [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md) | What the codebase contains and what changed |
| [GCP_SETUP.md](GCP_SETUP.md) | One-time GCP and Terraform commands |
| [GITHUB_ACTIONS.md](GITHUB_ACTIONS.md) | CI, deploy pipeline, required variables and secrets |
| [CLOUDFLARE_TUNNEL.md](CLOUDFLARE_TUNNEL.md) | Tunnel route, connector, token handling |
| [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) | Every variable, classified |
| [SECURITY.md](SECURITY.md) | Controls and recommendations |
| [ROLLBACK.md](ROLLBACK.md) | Application and database rollback |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Known failure modes |
| [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) | Cost drivers and levers |

## Status

| Step | Status |
|---|---|
| Application changes (Cloud Tasks worker, migrations, JSON logs, pools) | **Done**, 111 backend tests passing |
| Docker images (web, api, worker) | **Built locally**, non-root |
| Terraform (`infra/terraform`) | **Written; `fmt` and `validate` pass.** Not planned or applied: no access to the project from the build environment |
| GitHub Actions (`ci.yml` on PRs, compact `deploy-production.yml` on main, tunnel workflows) | **Written (compact).** Not executed on GitHub yet — still on `dev-hakym`, not merged |
| Live deployment, tunnel route, smoke test | **Blocked** until GCP_SETUP.md (Terraform apply + GitHub vars) is done by an admin |

## First deployment, in order

1. Grant an administrator `roles/owner` (or the roles listed in GCP_SETUP.md) on `absolute-access-415010`.
2. `infra/scripts/discover.sh`, then import any existing resources (`imports.tf.example`).
3. `infra/bootstrap/create-state-bucket.sh`, then `terraform init`, `terraform plan`, review, and `terraform apply`.
4. Set the GitHub variables printed by `terraform output github_actions_variables`; add the `CLOUDFLARE_TUNNEL_TOKEN` secret; create the `production` environment.
5. Run **Sync Cloudflare tunnel token**, then reset the connector VM.
6. Add the tunnel route `proof.beeneural.com → http://10.10.0.10:80` (dashboard, or **Configure Cloudflare tunnel route**).
7. Merge to `main`, or run **Deploy production** manually.
