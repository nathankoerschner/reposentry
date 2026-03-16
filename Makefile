.PHONY: install dev-backend dev-worker dev-frontend dev-all lint

# ---- Install ----
install:
	cd frontend && npm install
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
	cd worker  && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

# ---- Run individual services ----
dev-backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

dev-worker:
	cd worker && . .venv/bin/activate && uvicorn app.main:app --reload --port 8001

dev-frontend:
	cd frontend && npm run dev

# ---- Lint ----
lint:
	cd backend && . .venv/bin/activate && ruff check . && ruff format --check .
	cd worker  && . .venv/bin/activate && ruff check . && ruff format --check .
	cd frontend && npx tsc --noEmit

# ---- Database migrations ----
db-migrate:
	cd backend && . .venv/bin/activate && alembic upgrade head

db-revision:
	cd backend && . .venv/bin/activate && alembic revision --autogenerate -m "$(msg)"

# ---- Docker build ----
REGISTRY ?= us-central1-docker.pkg.dev/$(GCP_PROJECT)/reposentry
TAG ?= latest

docker-build:
	docker build -t $(REGISTRY)/backend:$(TAG) backend/
	docker build -t $(REGISTRY)/worker:$(TAG) worker/
	docker build -t $(REGISTRY)/frontend:$(TAG) frontend/

docker-push:
	docker push $(REGISTRY)/backend:$(TAG)
	docker push $(REGISTRY)/worker:$(TAG)
	docker push $(REGISTRY)/frontend:$(TAG)

# ---- Terraform ----
tf-init:
	cd infra && terraform init

tf-plan:
	cd infra && terraform plan

tf-apply:
	cd infra && terraform apply

tf-destroy:
	cd infra && terraform destroy
