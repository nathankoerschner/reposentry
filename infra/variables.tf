variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name (e.g. production, staging)"
  type        = string
  default     = "production"
}

# ---- Docker image tags ----

variable "backend_image" {
  description = "Full Docker image URL for the API service"
  type        = string
}

variable "worker_image" {
  description = "Full Docker image URL for the worker service"
  type        = string
}

variable "frontend_image" {
  description = "Full Docker image URL for the frontend service"
  type        = string
}

# ---- Database ----

variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "db_name" {
  description = "Postgres database name"
  type        = string
  default     = "reposentry"
}

# ---- Secrets (passed as sensitive vars or via Secret Manager) ----

variable "clerk_secret_key" {
  description = "Clerk secret key"
  type        = string
  sensitive   = true
}

variable "clerk_publishable_key" {
  description = "Clerk publishable key"
  type        = string
  sensitive   = true
}

variable "clerk_jwks_url" {
  description = "Clerk JWKS URL for JWT validation"
  type        = string
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
}
