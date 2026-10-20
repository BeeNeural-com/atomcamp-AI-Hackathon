terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state in a dedicated, versioned, access-controlled bucket created by
  # infra/bootstrap/create-state-bucket.sh. Configure at init time:
  #   terraform init -backend-config="bucket=<project>-beeproof-tfstate"
  backend "gcs" {
    prefix = "beeproof/production"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
