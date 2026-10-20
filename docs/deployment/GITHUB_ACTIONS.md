# GitHub Actions

| Workflow | Trigger | GCP |
|---|---|---|
| `ci.yml` | PRs into `main`; manual | none |
| `deploy-production.yml` | Push / merge to `main`; manual from `main` | WIF → `beeproof-github-deployer` |
| `sync-cloudflare-tunnel-token.yml` | Manual, `main` | WIF (secret version add) |
| `cloudflare-tunnel-config.yml` | Manual, `main` | Cloudflare API only |

## CI (`ci.yml`)

Runs on pull requests only (deploy does **not** re-run it):

- **backend:** ruff, compile, pytest, migrations + `alembic check`
- **frontend:** typecheck, unit tests, `next build`, Playwright e2e
- **terraform:** `fmt -check`, `init -backend=false`, `validate`

## Deploy (`deploy-production.yml`)

One job: **Build, migrate, deploy**. Concurrency `beeproof-production` (no cancel).

1. WIF auth → build/push `api`, `worker`, `web` (tag = `${GITHUB_SHA}`, deploy by digest)
2. Verify Cloud Run services + migrate job exist
3. Run `beeproof-migrate` (failure stops before traffic change)
4. Deploy revisions with `--no-traffic`, then shift 100%
5. Smoke test `https://proof.beeneural.com` (skippable via input or `PUBLIC_SMOKE_TEST=false`)
6. On failure after shift → restore previous revisions (migrations not reversed)

## Required configuration

**Variables:** `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT`  
Optional: `PUBLIC_SMOKE_TEST`, Cloudflare vars for tunnel workflows.

**Secrets:** `CLOUDFLARE_TUNNEL_TOKEN`, `CLOUDFLARE_API_TOKEN` (tunnel workflows only).

**Environment:** `production` (required reviewers recommended; deploy branch = `main`).

No GCP keys in GitHub. Actions are SHA-pinned; Dependabot bumps them weekly.
