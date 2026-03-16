terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Recommended: configure a remote backend for team use.
  # backend "gcs" {
  #   bucket = "reposentry-tfstate"
  #   prefix = "terraform/state"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---- Enable required APIs ----

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "compute.googleapis.com",
    "vpcaccess.googleapis.com",
    "servicenetworking.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ---- Artifact Registry ----

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "reposentry"
  format        = "DOCKER"
  description   = "Docker images for RepoSentry services"
  depends_on    = [google_project_service.apis]
}
