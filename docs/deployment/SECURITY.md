# Security

## Important: the application has no user authentication

Open access is a recorded product decision ([ADR-004](../product/ADR/ADR-004-open-access.md)).
There are no accounts, roles or organization isolation. Anyone who can reach `proof.beeneural.com` can upload,
review and export.

**Use only synthetic or public documents** until access control exists. If the hostname must be restricted
now, put **Cloudflare Access** in front of it. That needs no code change.

## Implemented controls

| Area | Control |
|---|---|
| CI/CD identity | GitHub OIDC → Workload Identity Federation. No service-account keys. Provider condition: this repository, `refs/heads/main`, `push`/`workflow_dispatch` only (no PRs, no forks) |
| Deployer least privilege | `run.developer`; `artifactregistry.writer` on the `beeproof` repository only; `iam.serviceAccountUser` on the four runtime SAs only; secret *version add* on the tunnel-token secret only; `logging.viewer`. It cannot change IAM, networking or the database |
| Runtime identities | One service account per component. Only the worker has Vertex AI and Document AI roles. Only the migrate job can read the migrator credential |
| Network exposure | Cloud Run ingress `internal` on all services. The only public path is Cloudflare → Tunnel → internal ALB. No VM has a public IP. The connector VM has no ingress firewall rules |
| Private worker | No route in the ALB. Invoker is only `beeproof-tasks-invoker` (OIDC from Cloud Tasks and Scheduler); custom audience pinned. The smoke test asserts `/internal/*` is not publicly reachable |
| Database | Private IP only, TLS required (`ENCRYPTED_ONLY`, `sslmode=require`). Separate migrator (schema owner) and runtime (DML-only) roles. Backups, PITR, deletion protection |
| Storage | Uniform bucket-level access, public access prevention enforced, versioning. Objects are streamed by the API; no public or signed links are issued |
| Secrets | Secret Manager for runtime secrets. GitHub secrets only for Cloudflare. TruffleHog in CI. `.gitignore` covers `.env`, tfvars and state |
| Uploads | Signature sniffing, extension/MIME consistency, 20 MB limit, page and pixel limits, generated storage keys, path-safe filenames |
| Abuse | Per-IP rate limit on mutating requests. Cloud Tasks caps concurrent AI work (`max_concurrent_dispatches`) |
| Untrusted content | Documents are data. Prompts instruct the model to ignore embedded instructions, outputs are schema-validated, and deterministic validators gate acceptance. Logs never contain document text |
| Containers | Non-root users (`app`, `node`). Trivy blocks fixable CRITICAL CVEs. Images referenced by digest at deploy time |
| Supply chain | Actions pinned by SHA, with Dependabot updates. Terraform provider checksums in `.terraform.lock.hcl` |
| Audit | Cloud Audit Logs (Admin Activity, on by default), the GitHub deployment history, the `deployment.json` artifact, and the application's append-only job trace |

## Recommendations (not automated)

1. **Cloudflare:** WAF managed rules; rate limit `POST /api/v1/*`; Bot Fight Mode; optionally Access.
2. Turn on Data Access audit logs for Secret Manager and Cloud Storage if compliance requires it.
3. Rotate the Cloudflare tunnel token: it was shared in a chat transcript during setup.
4. Restrict the Terraform state bucket to infrastructure administrators. It holds the generated DB passwords.
5. **Signed URLs:** downloads are proxied through the API today. If direct GCS downloads are added later, issue
   short-lived V4 signed URLs from the API's service account.
