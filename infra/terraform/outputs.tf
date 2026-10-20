output "github_actions_variables" {
  description = "Set these as GitHub repository variables (Settings -> Secrets and variables -> Actions -> Variables)."
  value = {
    GCP_PROJECT_ID                 = var.project_id
    GCP_REGION                     = var.region
    GCP_WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_DEPLOY_SERVICE_ACCOUNT     = google_service_account.sa["deployer"].email
  }
}

output "artifact_registry" {
  value = local.registry
}

output "cloud_run_services" {
  value = {
    web    = google_cloud_run_v2_service.web.name
    api    = google_cloud_run_v2_service.api.name
    worker = google_cloud_run_v2_service.worker.name
    job    = google_cloud_run_v2_job.migrate.name
  }
}

output "worker_url" {
  description = "Private worker URL (Cloud Tasks / Scheduler audience). Not reachable from the internet."
  value       = local.worker_url
}

output "internal_load_balancer_ip" {
  description = "Cloudflare Tunnel public hostname route target: http://<this IP>:80"
  value       = google_compute_address.ilb.address
}

output "cloudflare_tunnel_route" {
  value = "${var.domain} -> http://${google_compute_address.ilb.address}:80"
}

output "tasks_queue" {
  value = google_cloud_tasks_queue.jobs.id
}

output "documents_bucket" {
  value = google_storage_bucket.documents.name
}

output "cloud_sql_instance" {
  value = google_sql_database_instance.main.connection_name
}

output "tunnel_token_secret" {
  description = "Add the Cloudflare tunnel token as a version of this secret (never via Terraform)."
  value       = google_secret_manager_secret.tunnel_token.secret_id
}
