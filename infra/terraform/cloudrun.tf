# Cloud Run services and the migration job.
#
# Terraform owns configuration (identity, scaling, networking, env, secrets); GitHub Actions
# owns the running image and traffic. `ignore_changes` keeps `terraform apply` from rolling
# back a deployed release to the bootstrap image.
#
# Ingress is INTERNAL_ONLY everywhere: the run.app URLs are not reachable from the internet.
# web/api are reached through the internal load balancer (Cloudflare Tunnel connector);
# the worker only through Cloud Tasks / Cloud Scheduler with OIDC.

locals {
  backend_env = {
    APP_ENV                       = "production"
    LOG_FORMAT                    = "json"
    STORE                         = "gcs"
    BUCKET                        = google_storage_bucket.documents.name
    PROJECT                       = var.project_id
    AUTO_CREATE_TABLES            = "false"
    WORKER_MODE                   = "off"
    CLOUD_TASKS_QUEUE             = google_cloud_tasks_queue.jobs.id
    WORKER_SERVICE_URL            = local.worker_url
    TASKS_INVOKER_SERVICE_ACCOUNT = google_service_account.sa["tasks"].email
    CORS_ORIGINS                  = "https://${var.domain}"
    MAX_UPLOAD_BYTES              = "20971520"
  }
}

resource "google_cloud_run_v2_service" "web" {
  name                = "beeproof-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = true
  labels              = local.labels

  template {
    service_account                  = google_service_account.sa["web"].email
    max_instance_request_concurrency = 40
    timeout                          = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.web_max_instances
    }

    containers {
      image = var.bootstrap_image
      ports {
        container_port = 3000
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      startup_probe {
        tcp_socket {
          port = 3000
        }
        period_seconds    = 5
        failure_threshold = 12
        timeout_seconds   = 3
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].containers[0].image, template[0].revision, traffic]
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "api" {
  name                = "beeproof-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = true
  labels              = local.labels

  template {
    service_account                  = google_service_account.sa["api"].email
    max_instance_request_concurrency = 20
    timeout                          = "120s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
    }

    containers {
      image = var.bootstrap_image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.backend_env, {
          DB_POOL_SIZE       = "2"
          DB_MAX_OVERFLOW    = "2"
          RATE_LIMIT_PER_MIN = "120"
        })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      # The server binds its port only after startup (DB reachable, schema present),
      # so a TCP probe blocks traffic to a revision that cannot reach its database.
      startup_probe {
        tcp_socket {
          port = 8080
        }
        period_seconds    = 5
        failure_threshold = 24
        timeout_seconds   = 3
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].containers[0].image, template[0].revision, traffic]
  }

  depends_on = [
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_iam_member.database_url,
  ]
}

resource "google_cloud_run_v2_service" "worker" {
  name                = "beeproof-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = true
  custom_audiences    = [local.worker_url]
  labels              = local.labels

  template {
    service_account                  = google_service_account.sa["worker"].email
    max_instance_request_concurrency = 2
    timeout                          = "1800s" # matches the Cloud Tasks dispatch deadline maximum

    scaling {
      min_instance_count = 0
      max_instance_count = var.worker_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.main.id
        subnetwork = google_compute_subnetwork.main.id
      }
    }

    containers {
      image = var.bootstrap_image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.backend_env, {
          DB_POOL_SIZE       = "2"
          DB_MAX_OVERFLOW    = "1"
          EXTRACTOR          = local.extractor
          GEMINI_MODEL       = var.gemini_model
          GEMINI_LOCATION    = var.gemini_location
          LAYOUT_PROVIDER    = "auto"
          DOCAI_LOCATION     = var.docai_location
          DOCAI_PROCESSOR_ID = var.docai_processor_id
          PAGE_CONCURRENCY   = "4"
        })
        content {
          name  = env.key
          value = env.value
        }
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        tcp_socket {
          port = 8080
        }
        period_seconds    = 5
        failure_threshold = 24
        timeout_seconds   = 3
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].containers[0].image, template[0].revision, traffic]
  }

  depends_on = [
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_iam_member.database_url,
  ]
}

resource "google_cloud_run_v2_job" "migrate" {
  name                = "beeproof-migrate"
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.sa["migrate"].email
      max_retries     = 0
      timeout         = "900s"

      vpc_access {
        egress = "PRIVATE_RANGES_ONLY"
        network_interfaces {
          network    = google_compute_network.main.id
          subnetwork = google_compute_subnetwork.main.id
        }
      }

      containers {
        image   = var.bootstrap_image
        command = ["python", "-m", "beeproof.migrate"]
        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
        env {
          name  = "LOG_FORMAT"
          value = "json"
        }
        env {
          name  = "DB_APP_USER"
          value = google_sql_user.app.name
        }
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.migrator_database_url.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [client, client_version, template[0].template[0].containers[0].image]
  }

  depends_on = [
    google_secret_manager_secret_version.migrator_database_url,
    google_secret_manager_secret_iam_member.migrator_database_url,
  ]
}

# ---- invocation
# web/api: callable without Google identity, but only from inside the VPC (ingress INTERNAL_ONLY),
# i.e. through the internal load balancer. Requires that the org policy
# iam.allowedPolicyMemberDomains does not block allUsers; see docs/deployment/TROUBLESHOOTING.md.
resource "google_cloud_run_v2_service_iam_member" "web_public_via_lb" {
  name     = google_cloud_run_v2_service.web.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "api_public_via_lb" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# worker: only the Cloud Tasks / Scheduler identity.
resource "google_cloud_run_v2_service_iam_member" "worker_invoker" {
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sa["tasks"].email}"
}
