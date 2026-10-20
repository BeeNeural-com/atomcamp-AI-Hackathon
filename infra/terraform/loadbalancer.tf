# Regional internal Application Load Balancer: the private origin behind Cloudflare Tunnel.
#   Cloudflare edge -> Tunnel -> connector VM -> http://<internal_lb_ip> -> path routing:
#     /api, /api/*  -> beeproof-api   (serverless NEG)
#     everything else -> beeproof-web (serverless NEG)
# The worker has no route here and is never reachable through the tunnel.

resource "google_compute_region_network_endpoint_group" "web" {
  name                  = "beeproof-web-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.web.name
  }
}

resource "google_compute_region_network_endpoint_group" "api" {
  name                  = "beeproof-api-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }
}

resource "google_compute_region_backend_service" "web" {
  name                  = "beeproof-web-backend"
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group           = google_compute_region_network_endpoint_group.web.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }
}

resource "google_compute_region_backend_service" "api" {
  name                  = "beeproof-api-backend"
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group           = google_compute_region_network_endpoint_group.api.id
    balancing_mode  = "UTILIZATION"
    capacity_scaler = 1.0
  }
}

resource "google_compute_region_url_map" "main" {
  name            = "beeproof-ilb"
  region          = var.region
  default_service = google_compute_region_backend_service.web.id

  host_rule {
    hosts        = [var.domain]
    path_matcher = "beeproof"
  }

  path_matcher {
    name            = "beeproof"
    default_service = google_compute_region_backend_service.web.id

    path_rule {
      paths   = ["/api", "/api/*"]
      service = google_compute_region_backend_service.api.id
    }
  }
}

resource "google_compute_region_target_http_proxy" "main" {
  name    = "beeproof-ilb-proxy"
  region  = var.region
  url_map = google_compute_region_url_map.main.id
}

resource "google_compute_address" "ilb" {
  name         = "beeproof-ilb-ip"
  region       = var.region
  subnetwork   = google_compute_subnetwork.main.id
  address_type = "INTERNAL"
  address      = var.internal_lb_ip
}

resource "google_compute_forwarding_rule" "main" {
  name                  = "beeproof-ilb-http"
  region                = var.region
  load_balancing_scheme = "INTERNAL_MANAGED"
  ip_protocol           = "TCP"
  port_range            = "80"
  target                = google_compute_region_target_http_proxy.main.id
  network               = google_compute_network.main.id
  subnetwork            = google_compute_subnetwork.main.id
  ip_address            = google_compute_address.ilb.id
  network_tier          = "PREMIUM"

  depends_on = [google_compute_subnetwork.proxy_only]
}
