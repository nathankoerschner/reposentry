# RepoSentry

RepoSentry is an end-to-end security scanning app for public Python GitHub repositories.

It gives an AppSec engineer a basic workflow to:
- register a public GitHub repo
- trigger an asynchronous scan
- review structured findings
- triage findings per scan
- compare scans over time

## Architecture overview

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
                                 Git clone + Python file discovery
                                               |
                                               v
                           Stage 1 filter -> Stage 2 LLM investigation
                                               |
                                               v
                        Finding persistence + identity + scan comparison
```

### Services

| Service | Directory | Default port | Purpose |
| --- | --- | --- | --- |
| Frontend | `frontend/` | `5173` | Authenticated UI for repos, scans, findings, and triage |
| Backend API | `backend/` | `8000` | REST API, auth, persistence, scan creation, comparison |
| Worker | `worker/` | `8001` | Clones repos, runs the scanning pipeline, persists findings |

## Approach and key design decisions

I optimized for a product-shaped v1 rather than a perfect scanner.

The main decisions were:

1. **Separate API and worker services**  
   Scan execution is asynchronous and materially different from normal API traffic. Splitting the worker keeps long-running clone/LLM work away from request/response paths and makes queueing/concurrency easier to reason about.

2. **Two-stage scanning instead of “analyze the whole repo at once”**  
   Large repos make whole-repo prompting expensive and brittle. I first classify files by security relevance, then spend deeper analysis budget only on files likely to matter.

3. **Structured JSON everywhere at the LLM boundary**  
   The worker only accepts machine-readable JSON from the model. This keeps persistence and UI logic deterministic even if model prose would otherwise be more natural.

## Architecture decisions and tradeoffs

### Why the queue / worker split exists
- **Production path:** backend publishes a scan job to Pub/Sub; Pub/Sub pushes to the worker.
- **Development path:** backend calls the worker directly over HTTP.
- **Tradeoff:** this gives a realistic deployment shape without making local development painful.

This design lets the backend return quickly after creating a scan, while the worker owns:
- transitioning scan state from `queued -> running -> complete|failed`
- repository cloning
- file discovery
- LLM execution
- finding persistence
- cleanup

### Why scan status is persisted at both scan and file level
A repo scan can take a while, and failures are often partial. I store:
- scan-level status (`queued`, `running`, `complete`, `failed`)
- file-level processing state and errors

That makes the frontend comparison and progress APIs much more informative than a single opaque “job finished/failed” bit.

### Why the backend owns comparison and triage
Comparison and triage are product logic, not UI logic. Keeping them in the backend means:
- the frontend stays thin
- behavior is easier to test and evolve
- future clients could reuse the same API

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

### 3. Stage 1: cheap filtering for recall
Stage 1 exists to avoid spending deep-analysis budget on obviously irrelevant files.

The worker first applies lightweight heuristics for very obvious cases:
- structurally benign files can be marked `not_suspicious`
- risky paths like `routes`, `auth`, `admin`, etc. can be marked `suspicious`
- common dangerous patterns such as `eval`, `exec`, `subprocess`, `pickle`, `yaml.load`, SQL execution, route handlers, and filesystem access can short-circuit to `suspicious`

If heuristics do not decide, the LLM classifies the file as either:
- `suspicious`
- `not_suspicious`

The stage 1 prompt is intentionally recall-oriented: **when in doubt, keep the file in scope**.

### 4. Stage 2: deeper investigation on suspicious files
Suspicious files go through a multi-role investigation loop:
- **Investigator** proposes the strongest current hypothesis and requests minimal additional repo context
- **Challenger** tries to falsify or narrow that claim
- **Arbiter** decides whether there is a definitive issue, definitive no-issue, or whether another round is needed

The worker can gather additional evidence from the repo between rounds, such as:
- symbol definitions/usages
- imported file context
- class/method definitions
- dependency manifests

This design is a tradeoff:
- **Pros:** better grounded reasoning than a single one-shot prompt, especially for flows that cross files
- **Cons:** more latency, more token spend, and still not equivalent to real static taint analysis

### 5. Output parsing and malformed output handling
The worker expects strict JSON from every LLM role.

Current parsing flow:
1. request JSON-only output from the prompt
2. strip common markdown fences if the model still adds them
3. `json.loads(...)` the response
4. if parsing fails, retry with a repair prompt that includes the parse error
5. if retries still fail, fail that file and continue the rest of the scan

This was a deliberate resilience choice: a single bad model response should not invalidate the entire repo scan.

## Prompt design

### Stage 1 prompt design
The stage 1 prompt is simple on purpose:
- define what counts as `suspicious`
- define what counts as `not_suspicious`
- explicitly prioritize recall over precision
- force a tiny JSON schema with a one-sentence reason

That keeps stage 1 cheap and predictable.

### Stage 2 prompt design
The stage 2 prompts are stricter and more adversarial.

Important characteristics:
- each role is limited to repository-grounded evidence supplied in the prompt
- the arbiter is instructed not to declare a definitive issue without an exact repo file path and exact line number
- each round can request only a small number of new evidence items
- the prompts ask for structured fields like confidence, proof chain, blockers, and missing requirements

This is meant to reduce a common LLM failure mode: confidently inventing a vulnerability from weak evidence.

### Why I chose this prompting strategy
A one-pass “scan this file and list bugs” prompt is faster, but I found it too easy for the model to over-claim. The investigator/challenger/arbiter split is a product-quality tradeoff: higher latency in exchange for better discipline around evidence and exact anchors.

## How I handle LLM output parsing

The LLM boundary is intentionally narrow:
- prompts demand JSON only
- responses are parsed immediately
- malformed output is repaired and retried
- findings are normalized before persistence

I also validate/normalize fields such as:
- severity
- file path
- line number
- vulnerability type

If a finding points at a nonexistent file or produces unusable fields, the worker normalizes or drops it instead of trusting the raw output blindly.

## Token/context window strategy for larger codebases

I did **not** try to stuff an entire repository into one prompt. Instead, the strategy is:

1. **File-level processing**  
   The repo is scanned file by file.

2. **Two-stage narrowing**  
   Most files should stop at stage 1; only suspicious ones get more expensive analysis.

3. **Targeted evidence gathering**  
   Stage 2 can ask for specific extra context rather than receiving the whole repo.

4. **Prompt-size caps**  
   The worker caps file/context sizes and truncates evidence for prompts.
   Some of the guardrails in code include:
   - max file chars
   - max investigation rounds
   - max context snippets
   - max context chars
   - max evidence requests per iteration

5. **Repository index instead of repository dump**  
   Stage 2 receives a compact index of Python file paths so the model knows what exists without paying to inline everything.

### Tradeoff
This is much more scalable than whole-repo prompting, but it still has limits:
- vulnerabilities requiring broad global context may be missed
- non-Python artifacts are mostly invisible
- deeply distributed flows can exhaust the evidence budget before the model proves a claim

## Finding identity and stability across scans

For v1, each finding gets a fingerprint based on:
- normalized file path
- normalized vulnerability type

That fingerprint is used to:
- deduplicate findings within a scan
- attach new occurrences to an existing logical identity across scans
- drive `new`, `fixed`, and `persisting` comparisons

### Why this approach
I wanted something:
- deterministic
- easy to inspect and test
- stable across line-number drift
- cheap to compute

### Tradeoffs
This is intentionally coarse. It does **not** reliably handle:
- major file moves or refactors
- two issues of the same vulnerability type in the same file
- reclassification when the model renames the vuln type between scans

A stronger version would incorporate anchors such as code spans, AST locations, sink/source shape, or a learned canonicalization pass for vulnerability classes.

## What I would build next with another week

If I had another week, I would prioritize:

1. **Stronger finding identity**  
   Add code-span or AST-based anchors and vulnerability-type canonicalization.

2. **Incremental scanning**  
   Use git diff plus cached prior results so rescans are faster and cheaper.

3. **Recovery / reaper job**  
   Detect scans stuck in `running` after worker crashes or deploys.

4. **Better coverage outside raw Python files**  
   Add templates, config files, dependency manifests, and framework-specific hotspots.

5. **Model evaluation harness**  
   Measure recall/precision against seeded vulnerable repos and prompt variants.

6. **Richer UX for scan comparison**  
   Better diff explanations, grouping, and filtering by severity/vulnerability class.

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
createdb reposentry
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
f
