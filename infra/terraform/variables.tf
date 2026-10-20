variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Cloud SQL, Cloud Tasks, the load balancer and buckets."
  type        = string
  default     = "asia-south1"
}

variable "connector_zones" {
  description = "Zones for Cloudflare Tunnel connector VMs (one VM per zone, up to connector_count)."
  type        = list(string)
  default     = ["asia-south1-a", "asia-south1-b"]
}

variable "connector_count" {
  description = "Number of cloudflared connector VMs. 2 = high availability across zones."
  type        = number
  default     = 1
  validation {
    condition     = var.connector_count >= 1 && var.connector_count <= 2
    error_message = "connector_count must be 1 or 2."
  }
}

variable "connector_machine_type" {
  description = "Machine type for connector VMs (cloudflared needs very little)."
  type        = string
  default     = "e2-micro"
}

variable "enable_iap_ssh" {
  description = "Allow SSH to connector VMs through Identity-Aware Proxy only (no public IPs)."
  type        = bool
  default     = false
}

variable "domain" {
  description = "The single public hostname, served through Cloudflare Tunnel."
  type        = string
  default     = "proof.beeneural.com"
}

variable "github_repository" {
  description = "GitHub repository (owner/name) trusted by Workload Identity Federation."
  type        = string
  default     = "BeeNeural-com/atomcamp-AI-Hackathon"
}

variable "github_branch" {
  description = "Only workflows running on this branch can deploy."
  type        = string
  default     = "main"
}

variable "artifact_repository_id" {
  description = "Artifact Registry Docker repository name."
  type        = string
  default     = "beeproof"
}

variable "bootstrap_image" {
  description = "Placeholder image used only when Terraform first creates a Cloud Run service or job. CI/CD owns the real images."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

# ---- network
variable "subnet_cidr" {
  type    = string
  default = "10.10.0.0/24"
}

variable "proxy_subnet_cidr" {
  description = "Proxy-only subnet required by the regional internal Application Load Balancer."
  type        = string
  default     = "10.10.1.0/24"
}

variable "internal_lb_ip" {
  description = "Fixed private IP of the internal load balancer. Cloudflare Tunnel route: http://<this IP>:80."
  type        = string
  default     = "10.10.0.10"
}

# ---- database
variable "db_tier" {
  description = "Cloud SQL machine tier. db-g1-small allows ~50 connections; size pools accordingly."
  type        = string
  default     = "db-g1-small"
}

variable "db_availability_type" {
  description = "ZONAL (cheaper) or REGIONAL (high availability)."
  type        = string
  default     = "ZONAL"
}

variable "db_deletion_protection" {
  description = "Protects the production database from deletion by Terraform and the API."
  type        = bool
  default     = true
}

# ---- application
variable "gemini_model" {
  description = "Exact Vertex AI Gemini model ID available to the project (check Model Garden). Empty = text-layer extractor only."
  type        = string
  default     = ""
}

variable "gemini_location" {
  description = "Vertex AI location for Gemini (some models require 'global')."
  type        = string
  default     = "asia-south1"
}

variable "docai_location" {
  description = "Document AI location (us or eu)."
  type        = string
  default     = "us"
}

variable "docai_processor_id" {
  description = "Document AI Form Parser processor ID. Empty = no OCR witness for scans."
  type        = string
  default     = ""
}

variable "tasks_max_concurrent_dispatches" {
  description = "Upper bound on documents processed at once (backpressure for Gemini / Document AI quotas)."
  type        = number
  default     = 4
}

variable "tasks_max_dispatches_per_second" {
  type    = number
  default = 2
}

variable "api_max_instances" {
  type    = number
  default = 10
}

variable "web_max_instances" {
  type    = number
  default = 10
}

variable "worker_max_instances" {
  type    = number
  default = 10
}

# ---- operations
variable "alert_email" {
  description = "Email for monitoring alerts. Empty = alert policies without notification channels."
  type        = string
  default     = ""
}

variable "billing_account_id" {
  description = "Billing account ID for the budget alert (XXXXXX-XXXXXX-XXXXXX). Empty = no budget resource."
  type        = string
  default     = ""
}

variable "monthly_budget_amount" {
  description = "Monthly budget in the billing account currency."
  type        = number
  default     = 100
}

variable "enable_public_uptime_check" {
  description = "Uptime check against https://<domain>/api/v1/health. Enable after the Cloudflare route is live."
  type        = bool
  default     = false
}
