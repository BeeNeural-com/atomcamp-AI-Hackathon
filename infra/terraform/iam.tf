# Least-privilege identities. Every runtime component has its own service account.

locals {
  service_accounts = {
    web       = { id = "beeproof-web", name = "BeeProof frontend (Cloud Run)" }
    api       = { id = "beeproof-api", name = "BeeProof API (Cloud Run)" }
    worker    = { id = "beeproof-worker", name = "BeeProof document worker (Cloud Run)" }
    migrate   = { id = "beeproof-migrate", name = "BeeProof database migrations (Cloud Run Job)" }
    tasks     = { id = "beeproof-tasks-invoker", name = "OIDC identity for Cloud Tasks/Scheduler -> worker" }
    connector = { id = "beeproof-tunnel-connector", name = "Cloudflare Tunnel connector VM" }
    deployer  = { id = "beeproof-github-deployer", name = "GitHub Actions production deployer (WIF)" }
  }

  project_roles = {
    "api-logs"       = { sa = "api", role = "roles/logging.logWriter" }
    "api-metrics"    = { sa = "api", role = "roles/monitoring.metricWriter" }
    "api-trace"      = { sa = "api", role = "roles/cloudtrace.agent" }
    "worker-logs"    = { sa = "worker", role = "roles/logging.logWriter" }
    "worker-metrics" = { sa = "worker", role = "roles/monitoring.metricWriter" }
    "worker-trace"   = { sa = "worker", role = "roles/cloudtrace.agent" }
    "worker-vertex"  = { sa = "worker", role = "roles/aiplatform.user" }
    "worker-docai"   = { sa = "worker", role = "roles/documentai.apiUser" }
    "web-logs"       = { sa = "web", role = "roles/logging.logWriter" }
    "migrate-logs"   = { sa = "migrate", role = "roles/logging.logWriter" }
    "connector-logs" = { sa = "connector", role = "roles/logging.logWriter" }
    "connector-mets" = { sa = "connector", role = "roles/monitoring.metricWriter" }
    "deployer-run"   = { sa = "deployer", role = "roles/run.developer" }
    "deployer-logs"  = { sa = "deployer", role = "roles/logging.viewer" }
  }
}

resource "google_service_account" "sa" {
  for_each     = local.service_accounts
  account_id   = each.value.id
  display_name = each.value.name
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "roles" {
  for_each = local.project_roles
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.sa[each.value.sa].email}"
}

# ---- data access
resource "google_storage_bucket_iam_member" "documents" {
  for_each = toset(["api", "worker"])
  bucket   = google_storage_bucket.documents.name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.sa[each.key].email}"
}

resource "google_secret_manager_secret_iam_member" "database_url" {
  for_each  = toset(["api", "worker"])
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sa[each.key].email}"
}

resource "google_secret_manager_secret_iam_member" "migrator_database_url" {
  secret_id = google_secret_manager_secret.migrator_database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sa["migrate"].email}"
}

resource "google_secret_manager_secret_iam_member" "tunnel_token_reader" {
  secret_id = google_secret_manager_secret.tunnel_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.sa["connector"].email}"
}

# The deployer can add (not read) tunnel token versions, from the GitHub secret.
resource "google_secret_manager_secret_iam_member" "tunnel_token_writer" {
  secret_id = google_secret_manager_secret.tunnel_token.id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.sa["deployer"].email}"
}

# ---- Cloud Tasks: API and worker enqueue jobs; tasks carry the invoker's OIDC identity
resource "google_cloud_tasks_queue_iam_member" "enqueuer" {
  for_each = toset(["api", "worker"])
  name     = google_cloud_tasks_queue.jobs.name
  location = var.region
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.sa[each.key].email}"
}

resource "google_cloud_tasks_queue_iam_member" "viewer" {
  for_each = toset(["api", "worker"])
  name     = google_cloud_tasks_queue.jobs.name
  location = var.region
  role     = "roles/cloudtasks.viewer"
  member   = "serviceAccount:${google_service_account.sa[each.key].email}"
}

resource "google_service_account_iam_member" "act_as_tasks_invoker" {
  for_each           = toset(["api", "worker"])
  service_account_id = google_service_account.sa["tasks"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa[each.key].email}"
}

# ---- deployer: deploy revisions that run as the runtime identities, push images
resource "google_service_account_iam_member" "deployer_act_as" {
  for_each           = toset(["web", "api", "worker", "migrate"])
  service_account_id = google_service_account.sa[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sa["deployer"].email}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  location   = google_artifact_registry_repository.beeproof.location
  repository = google_artifact_registry_repository.beeproof.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.sa["deployer"].email}"
}

# ---------------------------------------------------------------- GitHub OIDC -> Workload Identity Federation
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "OIDC trust for ${var.github_repository} (production deploys only)"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "beeproof-github"
  display_name                       = "BeeProof GitHub"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.ref"              = "assertion.ref"
    "attribute.event_name"       = "assertion.event_name"
    "attribute.workflow_ref"     = "assertion.workflow_ref"
  }

  # Only this repository, only the production branch, never pull_request events (forks).
  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.ref == 'refs/heads/${var.github_branch}' && assertion.event_name in ['push', 'workflow_dispatch']"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.sa["deployer"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
