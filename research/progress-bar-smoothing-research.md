# Progress bar behavior and richer scan-progress research

## Question
Research how the current scan progress bar works, and how to smooth it out by having the system report progress on each file it runs.

Also: identify what **richer progress detail** we could show in the UI beyond a simple percentage — including what the LLM is doing, which stage/loop it is in, what evidence it is gathering, and where each file is in the analysis flow.

---

## Executive summary

The current progress bar is derived from file completion counts, but the worker does **not persist per-file progress incrementally** during execution. Because of that, the UI often sees stale data and the bar feels jumpy.

However, after reading the worker pipeline in detail, there is a much bigger opportunity than just smoothing the percentage bar:

**the system already has a lot of meaningful internal state that could be surfaced as scan progress.**

Today the UI only knows:
- scan status
- discovered file count
- whether each file is finished / failed / skipped

But the pipeline itself already has richer concepts:
- stage 1 vs stage 2
- suspicious vs not suspicious
- stage 2 investigation rounds
- investigator / challenger / arbiter roles
- evidence requests and evidence gathered
- current hypothesis and counter-hypothesis
- unresolved blockers
- confidence values per role
- exact verdict progression (`continue`, `definitive_issue`, `definitive_no_issue`, `uncertain`)

So the recommendation is twofold:

1. **Persist progress incrementally per file** so the bar becomes smooth.
2. **Add structured progress events / fields** so the UI can show rich, truthful detail about what the scan is doing.

If we do both, the experience can evolve from:
- "12%... 12%... 12%... 83%... done"

to something like:
- `Scanning 5 files concurrently`
- `214 / 1,103 files complete`
- `Current file: app/auth.py`
- `Stage 1: suspicious → entering deep investigation`
- `Round 2/5: challenger requested symbol usage for sanitize_redirect`
- `Evidence gathered: 3 snippets`
- `Arbiter: continue, needs exact sink line`

---

## Current implementation

## Frontend progress calculation
File: `frontend/src/pages/ScanDetailPage.tsx`

The scan detail page:
- polls every `3000ms`
- fetches:
  - `GET /api/scans/:scanId`
  - `GET /api/scans/:scanId/files`
- computes progress from file rows:

```ts
const processedFiles = files.filter((file) => file.processing_status !== null).length;
```

```ts
if (scan.status === "complete") return 100;
if (scan.status === "failed") {
  return files.length > 0 ? Math.round((processedFiles / files.length) * 100) : 100;
}
if (files.length === 0) return 12;
return Math.max(12, Math.round((processedFiles / files.length) * 100));
```

Behavior today:
- `complete` scans force `100%`
- `failed` scans use `processed / total`
- if no files are discovered yet, the UI shows a pseudo-indeterminate minimum of `12%`
- CSS animates width changes:

```css
.scan-progress-fill {
  transition: width 0.35s ease;
}
```

This is a fine rendering approach. The weak point is the freshness of the underlying progress data.

---

## Backend API today
File: `backend/app/routers/scans.py`

The frontend gets progress data from:
- `GET /api/scans/{scan_id}`
- `GET /api/scans/{scan_id}/files`

`/files` simply returns all `scan_files` rows. There is no dedicated progress endpoint, no summary object, and no streaming progress API.

There is also currently:
- no SSE
- no WebSocket
- no event feed for per-file state changes

---

## Data exposed today

### Scan file model
Files:
- `backend/app/models/scan_file.py`
- `backend/app/schemas/scans.py`
- `frontend/src/api.ts`

Per-file fields currently available:
- `file_path`
- `stage1_result`
- `stage2_attempted`
- `processing_status`
- `error_message`

### Current processing status enum
File: `backend/app/models/enums.py`

```py
class ProcessingStatus(str, enum.Enum):
    complete = "complete"
    failed = "failed"
    skipped = "skipped"
```

What is missing today:
- `queued`
- `running`
- timestamps like `started_at` / `completed_at`
- current stage / substage
- current iteration / max iterations
- current LLM role being executed
- evidence / blocker counters
- last progress message
- recent event history

So the frontend can only infer very coarse progress.

---

## Worker flow today

### Orchestration
File: `worker/app/services/scan_runner.py`

Current scan lifecycle:
1. mark scan `running`
2. clone repository
3. discover Python files
4. create `scan_files` rows
5. run LLM pipeline across files
6. persist findings
7. mark scan `complete`

### Concurrency
File: `worker/app/config.py`

```py
max_concurrent_files: int = 5
```

So the worker can analyze up to 5 files concurrently.

### File discovery
File: `worker/app/scanner/file_discovery.py`

Discovery is deterministic and sorted, and excludes directories like:
- `tests`
- `.venv`
- `venv`
- `site-packages`
- `build`
- `dist`
- `__pycache__`
- `.git`

This matters because the UI could show useful pre-analysis progress such as:
- files discovered
- files excluded implicitly by rules
- queue length before analysis starts

---

## How file analysis really works today
File: `worker/app/scanner/pipeline.py`

The pipeline is far richer than the current UI suggests.

## Stage 1: triage / suspiciousness classification
For each file:
1. read file content
2. if unreadable → fail file
3. if empty/tiny → skip file
4. run Stage 1 LLM classification

Possible Stage 1 outcomes:
- `suspicious`
- `not_suspicious`
- `failed`

If Stage 1 says:
- `not_suspicious` → file is done quickly
- `failed` → file fails
- `suspicious` → file enters Stage 2

### Important UI implication
Already, this gives at least three meaningful progress states we could show:
- quick-pass / harmless file
- deep investigation required
- failed before investigation

---

## Stage 2: iterative multi-role investigation loop
This is where the rich detail lives.

For suspicious files, Stage 2 runs a structured loop with three LLM roles:
- **Investigator**
- **Challenger**
- **Arbiter**

### Internal data structures already in code
The worker already defines:
- `InvestigatorOutput`
- `ChallengerOutput`
- `ArbiterOutput`
- `RoundRecord`
- `InvestigationState`
- `Stage2Outcome`

### InvestigationState already tracks
The in-memory state includes:
- `suspicious_file_path`
- `current_hypothesis`
- `counter_hypothesis`
- `specificity`
- `candidate_sink_lines`
- `candidate_sanitizers`
- `unresolved_blockers`
- `external_libraries_touched`
- `evidence_items`
- `round_history`
- `investigator_status`
- `challenger_status`
- `arbiter_status`
- `investigator_confidence`
- `challenger_confidence`
- `arbiter_confidence`

That is already enough to drive a very informative progress UI.

### Round flow
For each Stage 2 round:
1. run **investigator**
2. investigator proposes:
   - a hypothesis
   - candidate terminal sites
   - specificity level
   - evidence requests
   - known unknowns
   - falsification conditions
3. resolve investigator evidence requests against the repository
4. maybe run **challenger**
   - only when the hypothesis exists and specificity is high enough
5. challenger can:
   - refute
   - concede
   - ask for more context
   - request more evidence
   - add remaining concerns
6. resolve challenger evidence requests
7. run **arbiter**
   - decides `continue`, `definitive_issue`, or `definitive_no_issue`
8. update investigation state
9. append a `RoundRecord`
10. either stop or continue to the next round

### Stage 2 termination conditions
Stage 2 can end as:
- `definitive_issue`
- `definitive_no_issue`
- `uncertain`

That means we can show much better user-facing progress than just “file done” — we can show whether the file was:
- dismissed early
- escalated to deep review
- resolved confidently as safe
- resolved confidently as vulnerable
- left uncertain because evidence was insufficient

---

## Evidence gathering already exists
The Stage 2 loop supports repository evidence requests such as:
- `symbol_definition`
- `symbol_usage`
- `file`
- `import_resolution`
- `class_method_definition`
- `dependency_manifest`

And the worker already:
- resolves those requests
- merges evidence into state
- tracks `evidence_added` per round
- stores evidence snippets in `evidence_items`

### Important UI implication
This means the worker is already doing meaningful work that users would understand if surfaced, for example:
- `looked up definition of redirect_user`
- `resolved usages of sanitize_url`
- `read dependency manifest for django`
- `loaded class method definition AuthView.post`

A progress UI could absolutely show this.

---

## Logging already hints at richer progress
The worker logs per Stage 2 round:

```py
logger.info(
    "Stage 2 round file=%s round=%d investigator_conf=%.2f challenger=%s arbiter=%s evidence_added=%d blockers=%d",
    file_path,
    round_number,
    investigator.confidence,
    challenger.outcome if challenger is not None else "skipped",
    arbiter.verdict,
    round_record.evidence_added,
    len(state.unresolved_blockers),
)
```

This confirms the system already has structured, user-meaningful progress points such as:
- current file
- current round
- investigator confidence
- challenger outcome
- arbiter verdict
- evidence snippets added this round
- unresolved blocker count

The UI simply does not receive any of it today.

---

## Why the current progress bar feels jumpy

The real issue is not bar animation; it is **missing incremental state persistence**.

Today:
- file records are created up front
- file processing happens concurrently
- ORM objects are mutated during processing
- `db.flush()` happens only after the async pipeline finishes
- `db.commit()` happens in the outer runner after the whole pipeline returns

So the frontend often sees very little change while the worker is actively doing substantial work.

Result:
- long periods of no visible progress
- sudden jumps when state finally becomes visible
- poor observability into what the LLM is doing

---

## Key insight: we can show much more than a percent

The user asked whether there should be a lot of information we can show: details on what the LLM is doing, where in the loop it is, etc.

**Yes. Absolutely.**

Based on the existing pipeline, we can expose rich detail at three levels:

### Level 1: scan-wide summary
- total files discovered
- files queued
- files running
- files completed
- files skipped
- files failed
- files escalated to Stage 2
- total findings so far
- average current round among active Stage 2 files
- estimated active concurrency (`max_concurrent_files`)

### Level 2: per-file lifecycle
- current file path
- file status: queued / running / complete / failed / skipped
- current top-level phase:
  - reading
  - stage1
  - stage2
  - persisting
- stage1 result
- whether stage2 was entered
- start time / duration so far
- latest progress message

### Level 3: stage-2 deep investigation detail
For suspicious files in active investigation:
- current round / max rounds
- active role: investigator / challenger / arbiter
- current hypothesis
- counter-hypothesis
- specificity level
- candidate sink lines
- evidence request count
- evidence snippets gathered so far
- unresolved blocker count and examples
- external libraries touched
- investigator confidence
- challenger confidence
- arbiter confidence
- latest arbiter verdict (`continue`, etc.)

This is all grounded in existing pipeline state, not invented UX fluff.

---

## Recommended progress model

## 1. Add explicit file execution states
The `ProcessingStatus` enum is too coarse. Recommended replacement or expansion:

```py
queued
running
complete
failed
skipped
```

Add optional fields to `scan_files`:
- `started_at`
- `completed_at`
- `current_phase` (`reading`, `stage1`, `stage2`, `finalizing`)
- `current_role` (`investigator`, `challenger`, `arbiter`, null)
- `current_round`
- `max_rounds`
- `last_progress_message`
- `evidence_count`
- `blocker_count`
- `latest_hypothesis`
- `latest_counter_hypothesis`
- `latest_verdict`
- `findings_count_so_far`

These can be nullable and only populated for active/recent files.

## 2. Add a structured progress-event stream in storage
A simple and flexible model is to create a `scan_progress_events` table.

Example event types:
- `scan_started`
- `file_discovered`
- `file_started`
- `file_stage1_started`
- `file_stage1_completed`
- `file_stage2_started`
- `round_started`
- `investigator_completed`
- `evidence_resolved`
- `challenger_completed`
- `arbiter_completed`
- `file_completed`
- `file_failed`
- `scan_completed`

Suggested event payload examples:

```json
{
  "type": "file_stage1_completed",
  "file_path": "app/auth.py",
  "stage1_result": "suspicious"
}
```

```json
{
  "type": "round_started",
  "file_path": "app/auth.py",
  "round": 2,
  "max_rounds": 5
}
```

```json
{
  "type": "investigator_completed",
  "file_path": "app/auth.py",
  "round": 2,
  "hypothesis": "User-controlled redirect reaches HttpResponseRedirect without allowlist enforcement.",
  "specificity": "line_and_library",
  "confidence": 0.81,
  "requests": 2,
  "known_unknowns": ["Need definition of sanitize_redirect"]
}
```

```json
{
  "type": "arbiter_completed",
  "file_path": "app/auth.py",
  "round": 2,
  "verdict": "continue",
  "confidence": 0.73,
  "missing_requirements": ["Need exact sink line"]
}
```

This gives us both:
- current state snapshots
- an audit/event trail for the UI

## 3. Add a scan progress summary endpoint
Instead of making the frontend derive everything from a full file list, add something like:

`GET /api/scans/{scan_id}/progress`

Possible response:

```json
{
  "status": "running",
  "files_total": 1103,
  "files_queued": 879,
  "files_running": 5,
  "files_complete": 205,
  "files_failed": 8,
  "files_skipped": 6,
  "files_in_stage2": 3,
  "findings_so_far": 4,
  "latest_event_at": "2026-03-15T12:34:56Z",
  "active_files": [
    {
      "file_path": "app/auth.py",
      "current_phase": "stage2",
      "current_role": "challenger",
      "current_round": 2,
      "max_rounds": 5,
      "latest_hypothesis": "Open redirect through next param",
      "evidence_count": 3,
      "blocker_count": 1,
      "latest_verdict": "continue"
    }
  ],
  "recent_events": [
    {
      "type": "file_stage1_completed",
      "file_path": "payments/views.py",
      "stage1_result": "not_suspicious"
    }
  ]
}
```

This would enable a much better progress card than the current one.

---

## How to smooth the progress bar specifically

Even with richer detail, the bar itself still needs smoother updates.

### Core fix
Persist progress as each file starts and finishes.

### Recommended worker refactor
Current async flow uses `asyncio.gather(...)` and mutates SQLAlchemy model instances. For live progress, that is the wrong shape.

Recommended approach:
1. tasks return plain result / event objects
2. process task completions incrementally using `asyncio.as_completed(...)`
3. persist file state updates from one controlled writer path
4. commit after each event or in tiny batches

Suggested result object:

```py
@dataclass
class FileProcessingResult:
    scan_file_id: UUID
    file_path: str
    stage1_result: Stage1Result | None
    stage2_attempted: bool
    processing_status: ProcessingStatus
    error_message: str | None
    findings: list[FindingResult]
```

Suggested progress object:

```py
@dataclass
class FileProgressUpdate:
    scan_file_id: UUID
    file_path: str
    current_phase: str
    current_role: str | None = None
    current_round: int | None = None
    max_rounds: int | None = None
    message: str | None = None
    evidence_count: int | None = None
    blocker_count: int | None = None
    latest_hypothesis: str | None = None
    latest_counter_hypothesis: str | None = None
    latest_verdict: str | None = None
```

Then the worker can emit updates like:
- file started
- stage1 started
- stage1 suspicious
- round 1 investigator complete
- evidence added
- arbiter says continue
- round 2 started
- file definitive issue
- file complete

---

## Best UI possibilities

## Minimal upgrade
Keep the current progress card, but improve it to show:
- progress %
- files complete / total
- files currently running
- current queue size
- failures
- latest completed file

This alone would make the scan feel much more alive.

## Better upgrade
Add an “Active analysis” panel showing 3–5 currently running files.

For each active file:
- file path
- phase (`Stage 1` / `Stage 2`)
- round (`2 / 5`)
- role (`Challenger`)
- short progress message (`Requesting symbol usage for sanitize_redirect`)
- blocker / evidence counters

## Richest truthful upgrade
Add a recent activity feed:
- `app/auth.py → Stage 1 suspicious`
- `app/auth.py → Round 1 investigator hypothesis created`
- `app/auth.py → Evidence added: 2 snippets`
- `app/auth.py → Arbiter: continue`
- `payments/views.py → Stage 1 not suspicious`
- `core/redirects.py → File complete`

This would make the product feel much more transparent and technically sophisticated.

---

## How much of this is already available vs needs new work

## Already available in memory today
The worker already knows, during execution:
- which file is active
- whether stage1 flagged it suspicious
- whether stage2 is running
- current round number
- investigator/challenger/arbiter outputs
- evidence added per round
- blocker counts
- role confidences
- final verdict per file

## Not currently persisted or exposed
The worker does **not** currently store/expose:
- active/running file state
- per-round progress
- evidence events
- hypotheses / verdict snapshots
- current role in flight
- latest progress message

So the opportunity is very real, but it requires explicit progress persistence.

---

## Recommended implementation phases

## Phase 1: make existing bar truthful and smooth
1. add `queued` and `running` file states
2. persist file state changes incrementally
3. reduce polling from 3s to 1s while active
4. add scan-wide progress summary endpoint

Expected result:
- the bar moves smoothly
- UI can show active file count

## Phase 2: expose rich per-file state
1. add `current_phase`, `current_role`, `current_round`, `max_rounds`
2. persist short `last_progress_message`
3. persist `evidence_count`, `blocker_count`, and latest verdict
4. show active files panel in the UI

Expected result:
- users can see where the LLM is in the loop
- users understand why a file is taking time

## Phase 3: add recent activity / event model
1. add `scan_progress_events` table or equivalent append-only store
2. write events throughout file execution
3. expose `recent_events` in progress API
4. optionally surface historical playback / debugging views later

Expected result:
- high observability
- easier debugging of false positives / latency hotspots

## Phase 4: optional real-time streaming
1. add SSE endpoint for progress events
2. stream active scan updates to the UI
3. keep polling as fallback

Expected result:
- near-real-time progress UX

---

## Important implementation warning

The current worker mutates SQLAlchemy ORM `ScanFile` objects during concurrent file processing. That is a poor foundation for rich live progress.

For robust progress reporting, avoid using session-bound ORM instances as the live transport for concurrent tasks.

Better pattern:
- processing code returns plain structured updates/results
- a single controlled persistence layer writes them to DB
- commits happen incrementally

This is the safest path for both smooth progress and rich detail.

---

## Final recommendation

The right goal is not just “smooth the bar.”

The right goal is:

**turn scan execution into a first-class observable process.**

Concretely:
1. persist progress incrementally per file
2. add running/queued states
3. expose a progress summary endpoint
4. surface rich Stage 2 loop detail already present in the pipeline
5. optionally add an event feed and SSE later

The codebase already contains enough meaningful internal state to support a very rich progress UI:
- what stage the LLM is in
- what round it is on
- which role is active
- what evidence it requested
- what blockers remain
- how confidence is evolving
- whether the arbiter is converging or still uncertain

So yes: there should be a lot of information we can show, and the existing pipeline already gives us much of the raw material. The main missing piece is **persisting and exposing it as progress data**.
