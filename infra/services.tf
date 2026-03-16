# ---- Service accounts ----

resource "google_service_account" "backend" {
  account_id   = "reposentry-backend"
  display_name = "RepoSentry API Service"
}

resource "google_service_account" "worker" {
  account_id   = "reposentry-worker"
  display_name = "RepoSentry Scanner Worker"
}

# Grant backend the ability to publish to Pub/Sub
resource "google_pubsub_topic_iam_member" "backend_publish" {
  topic  = google_pubsub_topic.scan_jobs.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.backend.email}"
}

# ---- Cloud Run: API service ----

resource "google_cloud_run_v2_service" "backend" {
  name     = "reposentry-api"
  location = var.region

  template {
    service_account = google_service_account.backend.email

    volumes {
      name = "cloudsql"

      cloud_sql_instance {
        instances = [google_sql_database_instance.postgres.connection_name]
      }
    }

    containers {
      image = var.backend_image

      ports {
        container_port = 8000
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "PUBSUB_TOPIC_ID"
        value = google_pubsub_topic.scan_jobs.name
      }

      env {
        name  = "CLERK_PUBLISHABLE_KEY"
        value = var.clerk_publishable_key
      }

      env {
        name  = "CLERK_JWKS_URL"
        value = var.clerk_jwks_url
      }

      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = join(",", [
          "http://localhost:5173",
          google_cloud_run_v2_service.frontend.uri,
        ])
      }

      env {
        name  = "CORS_ALLOWED_ORIGIN_REGEX"
        value = "https://reposentry-frontend(?:-[a-z0-9]+)+(?:\\.[a-z0-9-]+)?\\.run\\.app"
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "CLERK_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.clerk_secret_key.secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
  }

  depends_on = [google_project_service.apis]
}

# Allow unauthenticated access to the API (Clerk handles auth at app level)
resource "google_cloud_run_v2_service_iam_member" "backend_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---- Cloud Run: Worker service ----

resource "google_cloud_run_v2_service" "worker" {
  name     = "reposentry-worker"
  location = var.region

  template {
    service_account = google_service_account.worker.email

    volumes {
      name = "cloudsql"

      cloud_sql_instance {
        instances = [google_sql_database_instance.postgres.connection_name]
      }
    }

    containers {
      image = var.worker_image

      ports {
        container_port = 8001
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_url.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.openai_api_key.secret_id
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "1Gi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    timeout = "900s"
  }

  depends_on = [google_project_service.apis]
}

# ---- Cloud Run: Frontend service ----

resource "google_cloud_run_v2_service" "frontend" {
  name     = "reposentry-frontend"
  location = var.region

  template {
    containers {
      image = var.frontend_image

      ports {
        container_port = 80
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "frontend_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.frontend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---- Construct DATABASE_URL secret ----

resource "google_secret_manager_secret" "db_url" {
  secret_id = "reposentry-database-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_url" {
  secret      = google_secret_manager_secret.db_url.id
  secret_data = "postgresql://${google_sql_user.app.name}:${random_password.db_password.result}@/${var.db_name}?host=/cloudsql/${google_sql_database_instance.postgres.connection_name}"
}

resource "google_secret_manager_secret_iam_member" "backend_db_url" {
  secret_id = google_secret_manager_secret.db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_db_url" {
  secret_id = google_secret_manager_secret.db_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_project_iam_member" "backend_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.backend.email}"
}

resource "google_project_iam_member" "worker_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.worker.email}"
}
}
}
