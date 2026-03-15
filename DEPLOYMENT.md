# Deployment

This project targets Google Cloud using Terraform.

## Provisioned infrastructure

Terraform in `infra/` creates:
- Cloud Run service for the backend API
- Cloud Run service for the worker
- Cloud Run service for the frontend
- Cloud SQL Postgres
- Pub/Sub topic and push subscription for scan jobs
- Secret Manager secrets for runtime credentials
- Artifact Registry repository for images
- service accounts and IAM bindings

## Important implementation details

### Frontend environment is build-time
The frontend is a Vite app. These values must exist at image build time:
- `VITE_CLERK_PUBLISHABLE_KEY`
- `VITE_API_BASE_URL`

The repo includes `cloudbuild.frontend.yaml` for that purpose.

### Development vs production dispatch
- In `development`, the backend calls the worker directly via `WORKER_URL`.
- In deployed environments, the backend publishes scan jobs to Pub/Sub.

### Cloud SQL connection style
Backend and worker are configured for Cloud Run + Cloud SQL socket access via `/cloudsql/...`.

## Prerequisites
- `gcloud`
- Terraform >= 1.5
- Docker or Cloud Build access
- A GCP project with billing enabled
- Clerk credentials
- OpenAI API key

## 1. Authenticate and select the project

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud auth configure-docker us-central1-docker.pkg.dev
```

## 2. Configure Terraform variables

```bash
cp infra/terraform.tfvars.example infra/terraform.tfvars
```

Fill in the project-specific values in `infra/terraform.tfvars`.

## 3. Provision infrastructure

```bash
make tf-init
make tf-plan
make tf-apply
```

After apply, inspect outputs:

```bash
cd infra && terraform output
```

Useful outputs include:
- `backend_url`
- `worker_url`
- `frontend_url`
- `db_instance_name`
- `artifact_registry`

## 4. Build and push images

### Option A: local Docker

```bash
export GCP_PROJECT=your-project-id
make docker-build
make docker-push
```

### Option B: Cloud Build
Backend and worker can be built with standard `gcloud builds submit` commands.
The frontend can be built with the included config so Vite build args are injected:

```bash
gcloud builds submit frontend \
  --config ../cloudbuild.frontend.yaml \
  --substitutions=_IMAGE=us-central1-docker.pkg.dev/YOUR_PROJECT/zeropath/frontend:TAG,_VITE_CLERK_PUBLISHABLE_KEY=pk_...,_VITE_API_BASE_URL=https://YOUR_BACKEND_URL
```

## 5. Point Terraform at the new images
Update the image references in `infra/terraform.tfvars`, then apply again:

```bash
make tf-apply
```

## 6. Verify deployment

```bash
curl https://YOUR_BACKEND_URL/
curl https://YOUR_BACKEND_URL/health
curl https://YOUR_WORKER_URL/
curl https://YOUR_WORKER_URL/health
```

You can also read logs with:

```bash
gcloud run services logs read zeropath-api --region us-central1
gcloud run services logs read zeropath-worker --region us-central1
gcloud run services logs read zeropath-frontend --region us-central1
```

## 7. Clerk configuration
In Clerk, allow the deployed frontend URL and configure redirect/callback URLs to match it.

## Notes and cleanup items
- Do not commit real secret values in `infra/terraform.tfvars`.
- Terraform state is currently local unless you configure a remote backend.
- The repo mentions a GitHub Actions deploy workflow, but deployment should be treated as manual unless that workflow and its secrets are fully configured.
- Moving to GitHub OIDC + Workload Identity Federation would be safer than long-lived service account keys.
