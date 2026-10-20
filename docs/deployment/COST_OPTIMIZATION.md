# Cost optimization

Approximate monthly costs for an idle or low-traffic hackathon deployment. **These are estimates. Verify
them in the Google Cloud pricing calculator for asia-south1**, because prices change.

| Resource | Default | Always-on cost driver | Lever |
|---|---|---|---|
| Cloud SQL `db-g1-small`, 10 GB SSD, backups, PITR | ZONAL | Largest fixed cost (tens of USD/month) | `db-f1-micro` for demos (about 25 connections: lower pools and max instances); stop the instance between events (`gcloud sql instances patch beeproof-pg --activation-policy NEVER`) |
| Internal Application LB | 1 forwarding rule | Hourly forwarding-rule charge plus proxy usage | Required by the Tunnel → Cloud Run design |
| Connector VM `e2-micro` | 1 VM | Small hourly charge | Keep `connector_count = 1` unless HA is needed |
| Cloud NAT | 1 gateway | Hourly charge plus data processed | Only the connector uses it |
| Cloud Run (web, api, worker) | `min_instance_count = 0` | None when idle | Keep min instances at 0; set `cpu_idle = true` (already) |
| Cloud Tasks / Scheduler | 1 queue / 1 job | Within free tiers at this volume | — |
| Artifact Registry | cleanup: keep 20 versions, delete untagged after 14 days | Storage | Already configured |
| Cloud Storage | versioned, noncurrent deleted after 30 days | Storage | Lifecycle rules are in Terraform |
| Vertex AI Gemini / Document AI | per call / per page | Usage | `tasks_max_concurrent_dispatches` caps spend rate; the re-look runs only on disputed fields; set `GEMINI_USD_PER_PAGE` / `DOCAI_USD_PER_PAGE` so the app reports estimated cost |

Not deployed on purpose:
- **Memorystore Redis:** Cloud Tasks replaces the queue.
- **Always-on instances:** none.
- **Second region.**
- **External load balancer:** Cloudflare Tunnel replaces it.

Budget: set `billing_account_id` and `monthly_budget_amount` in tfvars to get alerts at 50%, 90% and 100%.
