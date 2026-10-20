locals {
  labels = {
    app        = "beeproof"
    env        = "production"
    managed-by = "terraform"
  }

  apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "sqladmin.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudscheduler.googleapis.com",
    "aiplatform.googleapis.com",
    "documentai.googleapis.com",
    "secretmanager.googleapis.com",
    "compute.googleapis.com",
    "servicenetworking.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "billingbudgets.googleapis.com",
  ]

  registry  = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository_id}"
  extractor = var.gemini_model != "" ? "gemini" : "heuristic"
  # Deterministic Cloud Run URL; also registered as a custom audience on the worker for OIDC.
  worker_url = "https://beeproof-worker-${data.google_project.this.number}.${var.region}.run.app"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- Artifact Registry
resource "google_artifact_registry_repository" "beeproof" {
  location      = var.region
  repository_id = var.artifact_repository_id
  format        = "DOCKER"
  description   = "BeeProof AI container images (tagged with the git commit SHA)"
  labels        = local.labels

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent-releases"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
  cleanup_policies {
    id     = "delete-stale-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "1209600s" # 14 days
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Cloud Storage
resource "google_storage_bucket" "documents" {
  name                        = "${var.project_id}-beeproof-documents"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels

  # Prefixes: documents/ (originals, page previews), jobs/ (evidence crops, exports),
  # evaluations/ (benchmark reports). All objects are private; the API streams them.
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }
  lifecycle_rule {
    condition {
      age            = 180
      matches_prefix = ["evaluations/"]
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Secret Manager
resource "random_password" "db_app" {
  length  = 32
  special = false
}

resource "random_password" "db_migrator" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "beeproof-database-url"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql+psycopg://${google_sql_user.app.name}:${random_password.db_app.result}@${google_sql_database_instance.main.private_ip_address}:5432/${google_sql_database.beeproof.name}?sslmode=require"
}

resource "google_secret_manager_secret" "migrator_database_url" {
  secret_id = "beeproof-migrator-database-url"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "migrator_database_url" {
  secret      = google_secret_manager_secret.migrator_database_url.id
  secret_data = "postgresql+psycopg://${google_sql_user.migrator.name}:${random_password.db_migrator.result}@${google_sql_database_instance.main.private_ip_address}:5432/${google_sql_database.beeproof.name}?sslmode=require"
}

# The tunnel token is never stored in Terraform state: add versions out-of-band
# (workflow "Sync Cloudflare tunnel token" or `gcloud secrets versions add`).
resource "google_secret_manager_secret" "tunnel_token" {
  secret_id = "beeproof-cloudflare-tunnel-token"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Cloud SQL (PostgreSQL, private IP only)
resource "google_sql_database_instance" "main" {
  name                = "beeproof-pg"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = var.db_deletion_protection

  settings {
    tier                        = var.db_tier
    edition                     = "ENTERPRISE"
    availability_type           = var.db_availability_type
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    deletion_protection_enabled = var.db_deletion_protection
    user_labels                 = local.labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.main.id
      ssl_mode                                      = "ENCRYPTED_ONLY"
      enable_private_path_for_google_cloud_services = true
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "20:30" # UTC (02:00 IST)
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
      }
    }

    maintenance_window {
      day          = 7
      hour         = 21
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = false
      record_client_address   = false
    }
  }

  depends_on = [google_service_networking_connection.psa]
}

resource "google_sql_database" "beeproof" {
  name     = "beeproof"
  instance = google_sql_database_instance.main.name
}

# Owns the schema; used only by the migration job.
resource "google_sql_user" "migrator" {
  name     = "beeproof_migrator"
  instance = google_sql_database_instance.main.name
  password = random_password.db_migrator.result
}

# Runtime user: data access only (granted by the migration job, see beeproof/migrate.py).
resource "google_sql_user" "app" {
  name     = "beeproof_app"
  instance = google_sql_database_instance.main.name
  password = random_password.db_app.result
}

# ---------------------------------------------------------------- Cloud Tasks
resource "google_cloud_tasks_queue" "jobs" {
  name     = "beeproof-jobs"
  location = var.region

  rate_limits {
    max_dispatches_per_second = var.tasks_max_dispatches_per_second
    max_concurrent_dispatches = var.tasks_max_concurrent_dispatches
  }

  retry_config {
    max_attempts       = 5
    max_retry_duration = "3600s"
    min_backoff        = "10s"
    max_backoff        = "300s"
    max_doublings      = 4
  }

  stackdriver_logging_config {
    sampling_ratio = 1.0
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- Cloud Scheduler: recovery sweep
resource "google_cloud_scheduler_job" "sweep" {
  name             = "beeproof-worker-sweep"
  region           = var.region
  description      = "Recovers stale jobs and re-enqueues queued jobs whose task was lost"
  schedule         = "*/5 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "${local.worker_url}/internal/sweep"
    oidc_token {
      service_account_email = google_service_account.sa["tasks"].email
      audience              = local.worker_url
    }
  }

  depends_on = [google_project_service.apis, google_cloud_run_v2_service.worker]
}

# ---------------------------------------------------------------- Budget alert
resource "google_billing_budget" "monthly" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "beeproof-production-monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      units = tostring(var.monthly_budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    monitoring_notification_channels = local.notification_channels
  }
}
