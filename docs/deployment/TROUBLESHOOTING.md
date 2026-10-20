# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `google-github-actions/auth` fails: "unable to impersonate" / "permission denied" | Workflow not running on `refs/heads/main` from a push/dispatch; wrong `GCP_WORKLOAD_IDENTITY_PROVIDER`; repository renamed | Check the variables against `terraform output github_actions_variables`; the provider condition only trusts `BeeNeural-com/atomcamp-AI-Hackathon@main` |
| Deploy: "Cloud Run service … not found" | Terraform not applied yet | GCP_SETUP.md §3 |
| `terraform apply`: binding `allUsers` fails with "One or more users named in the policy do not belong to a permitted customer" | Org policy `iam.allowedPolicyMemberDomains` | Allow `allUsers` for this project, **or** switch web/api to authenticated invocation (needs an ALB with an identity-aware design) |
| Migration job fails: connection timeout | Job not on the VPC, or Private Service Access missing | `gcloud run jobs describe beeproof-migrate --region asia-south1` should show network interfaces; `terraform plan` should show no drift |
| Migration fails: "permission denied for schema public" | Migrations run as the runtime user | The job must use the `beeproof-migrator-database-url` secret |
| Revision never becomes ready | App cannot reach the DB (startup probe = server bind after DB check), or the image is wrong | `gcloud logging read 'resource.labels.service_name="beeproof-api" severity>=ERROR' --limit 20` |
| Jobs stay `queued` | Cloud Tasks cannot invoke the worker: OIDC audience, invoker IAM, or queue paused | `gcloud tasks queues describe beeproof-jobs --location asia-south1`; worker logs; the sweep re-enqueues every 5 min |
| Worker returns 403 to Cloud Tasks | Invoker binding missing, or the audience does not match | `worker_invoker` IAM member; the worker's `custom_audiences` must contain `WORKER_SERVICE_URL` |
| Project created before 2019-03-19: OIDC tasks rejected | Old Cloud Tasks service agent lacks token creation | Grant `roles/iam.serviceAccountTokenCreator` on `beeproof-tasks-invoker` to `service-<number>@gcp-sa-cloudtasks.iam.gserviceaccount.com` |
| `proof.beeneural.com` → Cloudflare 1033 / 502 | No healthy connector, or the route points elsewhere | Connector status (CLOUDFLARE_TUNNEL.md); route must be `http://10.10.0.10:80` |
| Cloudflare 502 / ALB 404 | Host header not `proof.beeneural.com` (URL map host rule) | Set "HTTP Host Header" in the tunnel route |
| Connector logs "tunnel token not available yet" | Secret has no version | Run **Sync Cloudflare tunnel token**, then reset the VM |
| Gemini 404 / "model not found" | Model ID not available in `gemini_location` | Use an ID listed by Model Garden; try `gemini_location = "global"` |
| AI 429 errors / `beeproof/ai_provider_errors` alert | Quota | Lower `tasks_max_concurrent_dispatches`; request a quota increase |
| Too many DB connections | Pools × instances above the tier limit (db-g1-small ≈ 50) | Lower max instances or pool sizes, or raise `db_tier` |
| Smoke test fails only on `revision` | The edge served a cached or old revision | Check the traffic split: `gcloud run services describe beeproof-api --region asia-south1 --format="value(status.traffic)"` |
| Local disk full during image builds | Docker build cache inside the WSL disk | `docker builder prune` (cache only), then compact the WSL disk if needed |
