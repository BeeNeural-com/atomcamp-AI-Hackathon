# Private network: Cloud Run (Direct VPC egress) -> Cloud SQL private IP,
# Cloudflare connector VM -> internal Application Load Balancer -> Cloud Run.

resource "google_compute_network" "main" {
  name                    = "beeproof-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "main" {
  name                     = "beeproof-${var.region}"
  region                   = var.region
  network                  = google_compute_network.main.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_10_MIN"
    flow_sampling        = 0.1
    metadata             = "EXCLUDE_ALL_METADATA"
  }
}

# Required by the regional internal Application Load Balancer (Envoy proxies).
resource "google_compute_subnetwork" "proxy_only" {
  name          = "beeproof-${var.region}-proxy-only"
  region        = var.region
  network       = google_compute_network.main.id
  ip_cidr_range = var.proxy_subnet_cidr
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# Private Service Access for Cloud SQL private IP.
resource "google_compute_global_address" "psa" {
  name          = "beeproof-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]
  deletion_policy         = "ABANDON"
}

# Outbound-only internet for the connector VMs (no public IPs anywhere).
resource "google_compute_router" "main" {
  name    = "beeproof-router"
  region  = var.region
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "main" {
  name                               = "beeproof-nat"
  router                             = google_compute_router.main.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.main.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# The VPC's implied rule already denies all ingress. Only optional IAP SSH is opened.
resource "google_compute_firewall" "iap_ssh" {
  count         = var.enable_iap_ssh ? 1 : 0
  name          = "beeproof-allow-iap-ssh-connector"
  network       = google_compute_network.main.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"] # Google IAP TCP forwarding
  target_tags   = ["beeproof-tunnel-connector"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
