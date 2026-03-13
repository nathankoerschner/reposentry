# ---- Pub/Sub topic + push subscription ----

resource "google_pubsub_topic" "scan_jobs" {
  name       = "scan-jobs"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "worker_push" {
  name  = "scan-jobs-worker-push"
  topic = google_pubsub_topic.scan_jobs.id

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.worker.uri}/scan"

    oidc_token {
      service_account_email = google_service_account.pubsub_invoker.email
    }
  }

  ack_deadline_seconds       = 600
  message_retention_duration = "604800s" # 7 days

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

# ---- Service account for Pub/Sub to invoke Cloud Run ----

resource "google_service_account" "pubsub_invoker" {
  account_id   = "pubsub-invoker"
  display_name = "Pub/Sub → Cloud Run Invoker"
}

resource "google_cloud_run_v2_service_iam_member" "pubsub_invoke_worker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_invoker.email}"
}
