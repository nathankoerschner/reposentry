# ---- Cloud SQL (Postgres) ----

resource "google_sql_database_instance" "postgres" {
  name             = "reposentry-${var.environment}"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    ip_configuration {
      # Enable public IP so Cloud Run can use the Cloud SQL connector/socket path.
      ipv4_enabled = true

      # Retain private networking as well.
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
  }

  deletion_protection = true
  depends_on          = [google_project_service.apis, google_service_networking_connection.private_vpc]
}

resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "app" {
  name     = "reposentry"
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

# ---- VPC for private Cloud SQL ----

resource "google_compute_network" "vpc" {
  name                    = "reposentry-vpc"
  auto_create_subnetworks = true
  depends_on              = [google_project_service.apis]
}

resource "google_compute_global_address" "private_ip" {
  name          = "reposentry-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

