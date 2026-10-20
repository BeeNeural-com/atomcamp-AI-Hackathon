# Cloudflare Tunnel connector(s): small VMs with no public IP running cloudflared as a
# hardened systemd service. Outbound-only (Cloud NAT); no ingress firewall rules.
# The tunnel token is read from Secret Manager at boot and never stored in Terraform.

resource "google_compute_instance" "connector" {
  count        = var.connector_count
  name         = "beeproof-tunnel-${count.index + 1}"
  zone         = var.connector_zones[count.index]
  machine_type = var.connector_machine_type
  tags         = ["beeproof-tunnel-connector"]
  labels       = merge(local.labels, { role = "cloudflared" })

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.main.id
    # no access_config block: no external IP
  }

  service_account {
    email  = google_service_account.sa["connector"].email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  metadata = {
    enable-oslogin         = "TRUE"
    block-project-ssh-keys = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/templates/cloudflared-startup.sh.tftpl", {
    project_id  = var.project_id
    secret_name = google_secret_manager_secret.tunnel_token.secret_id
  })

  allow_stopping_for_update = true

  depends_on = [
    google_compute_router_nat.main,
    google_secret_manager_secret_iam_member.tunnel_token_reader,
  ]
}
