# Actionable alerts. Log-based metrics match job state lines only - never document contents.

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "BeeProof production alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  depends_on = [google_project_service.apis]
}

locals {
  notification_channels = [for c in google_monitoring_notification_channel.email : c.id]
}

resource "google_logging_metric" "failed_jobs" {
  name        = "beeproof/failed_jobs"
  description = "Extraction jobs that ended in status failed"
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"beeproof-worker\" AND jsonPayload.message:\"finished with status failed\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "provider_errors" {
  name        = "beeproof/ai_provider_errors"
  description = "Gemini / Document AI errors (quota, timeout, invalid output)"
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"beeproof-worker\" AND jsonPayload.message=~\"PROVIDER_(QUOTA_EXCEEDED|TIMEOUT)|EXTRACTION_OUTPUT_INVALID|PARSER_FAILED\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "tunnel_not_ready" {
  name        = "beeproof/tunnel_not_ready"
  description = "cloudflared readiness check failures on connector VMs"
  filter      = "resource.type=\"gce_instance\" AND textPayload:\"cloudflared NOT READY\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "api_5xx" {
  display_name          = "BeeProof API 5xx responses"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "API 5xx > 10 in 5 min"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"beeproof-api\" AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "failed_jobs" {
  display_name          = "BeeProof failed extraction jobs"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "More than 3 failed jobs in 15 min"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.failed_jobs.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      duration        = "0s"
      aggregations {
        alignment_period     = "900s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "provider_errors" {
  display_name          = "BeeProof AI provider errors"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "More than 10 provider errors in 15 min"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.provider_errors.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "0s"
      aggregations {
        alignment_period     = "900s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "queue_backlog" {
  display_name          = "BeeProof job queue backlog"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "More than 50 queued tasks for 10 min"
    condition_threshold {
      filter          = "resource.type = \"cloud_tasks_queue\" AND resource.labels.queue_id = \"${google_cloud_tasks_queue.jobs.name}\" AND metric.type = \"cloudtasks.googleapis.com/queue/depth\""
      comparison      = "COMPARISON_GT"
      threshold_value = 50
      duration        = "600s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "sql_cpu" {
  display_name          = "BeeProof Cloud SQL CPU"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "CPU above 80% for 10 min"
    condition_threshold {
      filter          = "resource.type = \"cloudsql_database\" AND resource.labels.database_id = \"${var.project_id}:${google_sql_database_instance.main.name}\" AND metric.type = \"cloudsql.googleapis.com/database/cpu/utilization\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "600s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "tunnel" {
  display_name          = "BeeProof Cloudflare Tunnel connector not ready"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "Readiness check failing for 5 min"
    condition_threshold {
      filter          = "resource.type = \"gce_instance\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.tunnel_not_ready.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      duration        = "0s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}

resource "google_monitoring_uptime_check_config" "public" {
  count        = var.enable_public_uptime_check ? 1 : 0
  display_name = "BeeProof public health (${var.domain})"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/api/v1/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.domain
    }
  }

  content_matchers {
    content = "\"status\":\"ok\""
    matcher = "CONTAINS_STRING"
  }
}

resource "google_monitoring_alert_policy" "public_uptime" {
  count                 = var.enable_public_uptime_check ? 1 : 0
  display_name          = "BeeProof public endpoint down"
  combiner              = "OR"
  notification_channels = local.notification_channels
  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.public[0].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
}
