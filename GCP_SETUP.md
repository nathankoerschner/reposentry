# GCP Setup Notes

This file captures the first successful Google Cloud rollout for the project.
It is a historical reference, not the primary source of truth for deployment.
Use `DEPLOYMENT.md` and `infra/` for the current deploy flow.

## Summary
The project was deployed to a dedicated GCP project with:
- Cloud Run services for frontend, backend, and worker
- Cloud SQL Postgres
- Pub/Sub for scan jobs
- Artifact Registry for images
- Secret Manager for runtime secrets

## Notable decisions from the initial rollout

### 1. Cloud Build was used for image builds
Local Docker was unreliable during the first deployment, so images were built in GCP instead.
The frontend uses `cloudbuild.frontend.yaml` so Vite build-time variables can be injected.

### 2. Frontend values must be provided at build time
The frontend Docker build accepts:
- `VITE_CLERK_PUBLISHABLE_KEY`
- `VITE_API_BASE_URL`

### 3. Cloud SQL uses the standard Cloud Run socket integration
The initial attempt used a Serverless VPC Access connector. That was replaced with the more direct Cloud Run Cloud SQL integration:
- backend/worker mount `/cloudsql`
- `DATABASE_URL` uses socket-style connection syntax
- service accounts need `roles/cloudsql.client`

## Files that were introduced or adjusted during the rollout
- `cloudbuild.frontend.yaml`
- `frontend/Dockerfile`
- `infra/main.tf`
- `infra/database.tf`
- `infra/services.tf`
- `infra/terraform.tfvars`

## Follow-up cleanup still worth doing
- move Terraform state to a remote backend
- avoid keeping real secrets in local tfvars files
- formalize CI/CD if GitHub Actions deploys are desired
- add custom domains if this becomes a long-lived deployment
