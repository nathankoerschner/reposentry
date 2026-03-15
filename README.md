# ZeroPath

ZeroPath is a small end-to-end security scanning app for public Python GitHub repositories.

It gives AppSec engineers a basic workflow to:
- register a public GitHub repo
- trigger an asynchronous scan
- review structured findings
- triage findings per scan
- compare scans over time

## Architecture

```text
Frontend (React/Vite + Clerk)
        |
        v
Backend API (FastAPI + Postgres) ----> Pub/Sub topic (production)
        |                                      |
        | development: direct HTTP            v
        +--------------------------------> Worker (FastAPI)
                                               |
                                               v
                                     LLM-based Python scanner
```

### Services

| Service | Directory | Default port | Purpose |
| --- | --- | --- | --- |
| Frontend | `frontend/` | `5173` | Authenticated UI |
| Backend API | `backend/` | `8000` | Repositories, scans, findings, comparison, triage |
| Worker | `worker/` | `8001` | Executes scan jobs and writes results |

## How scanning works

### 1. Repository intake
- Only public `https://github.com/{owner}/{repo}` URLs are accepted.
- The worker does a shallow clone of the repository default branch.
- The exact commit SHA is stored on the scan.

### 2. File discovery
- Only `.py` files are scanned in v1.
- Default exclusions:
  - `tests/`
  - `.venv/`
  - `venv/`
  - `site-packages/`
  - `build/`
  - `dist/`
  - `__pycache__/`
  - `.git/`

### 3. Two-stage LLM analysis
- **Stage 1:** classify each file as `suspicious` or `not_suspicious`
- **Stage 2:** deeply analyze suspicious files and return structured findings

Stage 1 is tuned for recall. When uncertain, it prefers to keep a file in scope rather than skip it.

### 4. Output parsing and repair
The worker expects strict JSON from the model.

If parsing fails it:
1. strips common markdown fences
2. retries with a repair prompt
3. marks that file as failed if parsing still fails
4. continues the overall scan

### 5. Finding identity
Each finding gets a stable fingerprint based on:
- normalized file path
- normalized vulnerability type

That is intentionally simple. It works reasonably well for comparison across nearby scans, but it does **not** survive major refactors or distinguish multiple issues of the same type in one file.

## Current product behavior

### Repository endpoints
- `POST /api/repositories`
- `GET /api/repositories`
- `GET /api/repositories/{id}`
- `DELETE /api/repositories/{id}`

### Scan endpoints
- `POST /api/repositories/{id}/scans`
- `GET /api/repositories/{id}/scans`
- `GET /api/scans/{id}`
- `DELETE /api/scans/{id}`
- `GET /api/scans/{id}/files`
- `GET /api/scans/{id}/findings`

### Comparison and triage
- `GET /api/repositories/{id}/compare?base_scan_id=...&target_scan_id=...`
- `PATCH /api/finding-occurrences/{id}/triage`

### Health checks
- Backend: `GET /`
- Backend health: `GET /health`
- Worker: `GET /`
- Worker health: `GET /health`

## Local development

### Prerequisites
- Python 3.11+
- Node 20+
- PostgreSQL 14+
- Git
- Clerk account
- OpenAI API key

### 1. Install dependencies

```bash
make install
```

### 2. Configure environment

```bash
cp .env.example .env
```

Important variables:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Postgres connection string |
| `CLERK_SECRET_KEY` | Backend Clerk secret |
| `CLERK_PUBLISHABLE_KEY` | Frontend Clerk key |
| `CLERK_JWKS_URL` | Clerk JWKS URL |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_MODEL` | Model name for scanning |
| `VITE_API_BASE_URL` | Frontend API base URL |
| `WORKER_URL` | Backend-to-worker URL for development dispatch |
| `ENVIRONMENT` | Use `development` locally |

The project expects a shared root `.env`. If needed:

```bash
ln -sf ../.env backend/.env
ln -sf ../.env worker/.env
ln -sf ../.env frontend/.env
```

### 3. Run migrations

```bash
createdb zeropath
make db-migrate
```

### 4. Start services

```bash
make dev-backend
make dev-worker
make dev-frontend
```

### 5. Verify

```bash
curl http://localhost:8000/
curl http://localhost:8000/health
curl http://localhost:8001/
curl http://localhost:8001/health
```

Then open `http://localhost:5173`.

## Deployment docs

- `DEPLOYMENT.md` — current deployment flow and infrastructure notes
- `GCP_SETUP.md` — historical notes from the first GCP rollout
- `PRD.md` — original product requirements
- `frompartner.md` — original challenge brief

## Known limitations

- Python files only
- No cross-file taint tracking in the traditional static-analysis sense
- Template/config scanning is not implemented
- LLM output can vary between runs
- A worker crash can leave a scan in `running`
- Finding identity is intentionally coarse

## What to improve next

1. Better finding identity using code spans / AST anchors
2. Incremental scans based on git diff
3. Template and config scanning
4. Stale scan recovery / reaper job
5. Better scan progress and richer comparison UX
6. Shared Python package for duplicated backend/worker models if the project grows further
