# Progress Bar Smoothing and Rich Scan Progress Implementation Plan

Date: 2026-03-15
Source: `research/progress-bar-smoothing-research.md`

## Goal

Make scan progress feel smooth, truthful, and informative by:

- persisting file progress incrementally during worker execution
- exposing scan-wide progress summaries instead of forcing the frontend to infer everything from full file lists
- adding richer per-file execution state for active analysis
- creating a foundation for recent activity / event-style progress updates

The implementation should preserve the current scan pipeline architecture while improving observability step by step.

## Current Code Touchpoints

Primary files:
- `frontend/src/pages/ScanDetailPage.tsx`
- `frontend/src/api.ts`
- `backend/app/routers/scans.py`
- `backend/app/schemas/scans.py`
- `backend/app/models/scan_file.py`
- `backend/app/models/enums.py`
- `worker/app/services/scan_runner.py`
- `worker/app/scanner/pipeline.py`
- `worker/app/config.py`

Likely related areas:
- database migration setup for backend/worker shared models
- any SQLAlchemy model serialization code used by `/api/scans/:scanId/files`
- frontend scan detail components that currently render only a percentage + file table

## Implementation Principles

1. **Fix data freshness before polishing animation.** The main issue is stale persisted state, not CSS.
2. **Prefer truthful progress over fake interpolation.** If the UI moves more often, it should be because the system actually persisted new state.
3. **Ship in layers.** First make the existing bar accurate, then enrich the data model, then add event-style progress.
4. **Avoid concurrent mutation of session-bound ORM objects as the progress transport.** Prefer structured updates/results with controlled persistence.
5. **Keep polling support even if streaming is added later.** Polling should remain the compatibility baseline.

---

## Phase 0: Baseline the Existing Progress Flow

### Deliverables
- Clear understanding of current progress calculation and persistence timing
- A short implementation note on where progress becomes stale in the worker

### Tasks
- Confirm the current frontend progress calculation in `frontend/src/pages/ScanDetailPage.tsx`:
  - percent derived from `processing_status !== null`
  - 12% floor before files are available
  - 3s polling cadence
- Trace current backend/worker state transitions through:
  - `worker/app/services/scan_runner.py`
  - `worker/app/scanner/pipeline.py`
  - `backend/app/routers/scans.py`
- Verify exactly when `scan_files` rows are:
  - created
  - updated
  - flushed
  - committed
- Add temporary debug logging if needed to confirm when progress becomes visible to the UI.

### Success criteria
- We can point to the exact reason the bar stays stale during active processing
- We know which state transitions need incremental persistence first

---

## Phase 1: Add Explicit File Lifecycle States

### Deliverables
- `scan_files` can represent queued and running work explicitly
- frontend can distinguish queued vs active vs terminal file states

### Tasks
Update the processing status enum in `backend/app/models/enums.py` (and any mirrored/shared enum locations) to support:

```py
queued
running
complete
failed
skipped
```

Update `scan_file` model/schema/API exposure to reflect the new states.

Recommended field additions in `backend/app/models/scan_file.py` and corresponding schemas:
- `started_at`
- `completed_at`

At minimum, implement these lifecycle transitions:
- discovered file → `queued`
- worker begins file analysis → `running`
- file exits normally → `complete` or `skipped`
- file exits abnormally → `failed`

### Files likely affected
- `backend/app/models/enums.py`
- `backend/app/models/scan_file.py`
- `backend/app/schemas/scans.py`
- worker-side imports/usages of `ProcessingStatus`
- DB migration files

### Success criteria
- Newly discovered files no longer appear as “unknown until terminal”
- UI can count queued, running, and completed files separately

---

## Phase 2: Persist Progress Incrementally During Worker Execution

### Deliverables
- File progress becomes visible while the scan is still running
- The existing percent bar updates steadily as work completes

### Tasks
Refactor worker progress handling so updates are persisted as work advances rather than only after the full async pipeline returns.

Recommended approach:
1. Stop relying on concurrent mutation of session-bound ORM `ScanFile` instances as the primary progress mechanism.
2. Have file-processing tasks emit plain structured results and progress updates.
3. Use a controlled persistence path in `scan_runner.py` and/or `pipeline.py` to write updates incrementally.
4. Commit after meaningful progress transitions or in very small batches.

Recommended progress update shape:

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

Recommended result handling change:
- prefer `asyncio.as_completed(...)` or another incremental completion pattern over waiting for all tasks before persisting visible state

### Minimum state transitions to persist immediately
- file marked `running`
- file reaches Stage 1 result
- file marked terminal (`complete`, `failed`, `skipped`)

### Success criteria
- The progress bar advances steadily as files finish
- Active file counts change during the scan, not only near the end
- Progress survives normal polling without needing frontend hacks

---

## Phase 3: Add Scan Progress Summary API

### Deliverables
- Dedicated progress endpoint optimized for the scan detail page
- frontend no longer derives all progress state from the full files list

### Tasks
Add a new endpoint in `backend/app/routers/scans.py`:

`GET /api/scans/{scan_id}/progress`

Recommended response shape:

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
  "active_files": [],
  "recent_events": []
}
```

Initial MVP can omit `recent_events` if not yet implemented, but should support:
- total file counts
- queued/running/complete/failed/skipped counts
- active file summaries

Add backend schema types in `backend/app/schemas/scans.py` and matching frontend types in `frontend/src/api.ts`.

### Success criteria
- Scan detail page can fetch one concise progress object for the header/progress card
- The UI no longer needs to compute all high-level progress from raw file rows

---

## Phase 4: Enrich Per-File Progress State

### Deliverables
- Active files expose their current phase and Stage 2 details
- UI can show what the worker is doing, not just how many files finished

### Tasks
Add nullable progress metadata fields to `scan_files` or another current-state store.

Recommended fields:
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
- optional `findings_count_so_far`

Update `worker/app/scanner/pipeline.py` to emit these values at meaningful points:
- file read started
- Stage 1 started/completed
- suspicious file entering Stage 2
- round started
- investigator completed
- challenger completed
- arbiter completed
- file finalized

### Important guidance
Keep fields nullable and scoped to active/recent work so this remains operationally simple. The goal is visibility, not perfect historical reconstruction yet.

### Success criteria
- Active scan progress can show which files are in Stage 1 vs Stage 2
- For suspicious files, current round and role are visible
- Long-running files have an understandable “what is happening” message

---

## Phase 5: Upgrade the Scan Detail UI

### Deliverables
- Smoother progress bar backed by truthful data
- A visible “active analysis” panel using the new progress endpoint

### Tasks
Update `frontend/src/pages/ScanDetailPage.tsx` to:
- fetch the new `/progress` endpoint while the scan is active
- reduce polling interval during active scans from 3000ms to ~1000ms if acceptable
- keep file list fetching for detailed tables, but stop using it as the only summary source

Recommended UI changes:

#### Progress card
Show:
- progress percent
- files complete / total
- running count
- queued count
- failed count

#### Active analysis panel
For 3–5 active files, show:
- file path
- phase (`Stage 1` / `Stage 2`)
- current round (`2 / 5`)
- current role (`Challenger`)
- last progress message
- evidence / blocker counts

#### Preserve graceful fallback behavior
If richer data is absent for older scans or partial rollout:
- fall back to coarse percent
- do not break the current scan detail page

### Success criteria
- The page feels active even when few files complete in a given minute
- Users can tell which files are being investigated deeply and why

---

## Phase 6: Add Recent Progress Events

### Deliverables
- Append-only progress event model or equivalent event storage
- UI can render a recent activity feed

### Tasks
Create a progress event persistence mechanism, ideally a new table such as `scan_progress_events`.

Recommended event types:
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

Each event should store:
- scan id
- timestamp
- type
- file path if applicable
- compact JSON payload for role/round/message details

Expose a bounded recent event list from `/progress` or a dedicated events endpoint.

### Example event payloads

```json
{
  "type": "file_stage1_completed",
  "file_path": "app/auth.py",
  "stage1_result": "suspicious"
}
```

```json
{
  "type": "arbiter_completed",
  "file_path": "app/auth.py",
  "round": 2,
  "verdict": "continue",
  "confidence": 0.73
}
```

### Success criteria
- Recent scan activity is visible without reading worker logs
- We have a historical trail for debugging stuck or confusing scans

---

## Phase 7: Optional Real-Time Streaming

### Deliverables
- SSE-based near-real-time progress delivery
- polling remains as fallback

### Tasks
Once polling + events are stable, add an SSE endpoint such as:

`GET /api/scans/{scan_id}/progress/stream`

Use it to push:
- progress summary refreshes, or
- event-by-event updates

Frontend behavior:
- use SSE for active scans when available
- fall back to polling automatically

### Success criteria
- Active scan updates appear nearly immediately
- No regression in environments where SSE is unavailable

---

## Data Model Recommendation

### Minimum model changes for Phase 1–5
Add to `scan_files`:
- `processing_status` extended with queued/running
- `started_at`
- `completed_at`
- optional progress snapshot fields listed above

### Event model for Phase 6+
Create `scan_progress_events` with fields like:
- `id`
- `scan_id`
- `scan_file_id` nullable
- `file_path` nullable
- `event_type`
- `payload_json`
- `created_at`

This gives both:
- current-state snapshots on `scan_files`
- historical progress events for activity feeds and debugging

---

## Recommended Rollout Order

Safest path:

1. **Phase 0–2**: baseline current behavior, add queued/running, persist state incrementally
2. **Phase 3**: add progress summary endpoint
3. **Phase 5**: switch frontend progress card to the summary endpoint
4. **Phase 4**: enrich per-file current-state metadata for active files
5. **Phase 6**: add recent activity events
6. **Phase 7**: add SSE if needed

This order gets the core UX win early without blocking on the full event model.

---

## Concrete File-by-File Plan

### `worker/app/services/scan_runner.py`
- refactor scan execution to persist progress incrementally
- centralize progress/result writing rather than relying on end-of-run flush/commit only
- handle file task completion incrementally

### `worker/app/scanner/pipeline.py`
- emit structured per-file progress updates for stage/role/round transitions
- update progress snapshot fields during Stage 1 and Stage 2 work
- keep file-level analysis logic intact while surfacing state

### `backend/app/models/enums.py`
- expand `ProcessingStatus` with `queued` and `running`

### `backend/app/models/scan_file.py`
- add progress snapshot columns such as timestamps, phase, role, round, message, and counters

### `backend/app/schemas/scans.py`
- add progress response schemas
- expose new per-file fields as needed

### `backend/app/routers/scans.py`
- add `/api/scans/{scan_id}/progress`
- optionally add SSE/events endpoints later

### `frontend/src/api.ts`
- add types and fetcher for scan progress summary

### `frontend/src/pages/ScanDetailPage.tsx`
- switch header/progress card to the summary endpoint
- add active analysis UI
- reduce active polling interval if acceptable

---

## Acceptance Criteria

The implementation is complete when:

1. The scan progress bar updates during active work rather than remaining stale for long periods
2. Files have explicit queued/running/terminal lifecycle states
3. The frontend consumes a dedicated scan progress summary endpoint
4. Active files expose at least phase and basic progress detail
5. The scan detail page shows more than a bare percentage during long-running scans
6. The worker persistence approach is robust under concurrent file processing
7. Optional event history can explain recent scan activity without reading raw logs

---

## Open Questions To Resolve During Implementation

1. Should the richer per-file progress fields live directly on `scan_files`, or should some current-state data be computed from an events table?
2. How frequently can we commit progress updates without hurting scan throughput materially?
3. Should the progress summary endpoint compute counts live from `scan_files`, or should some values be denormalized on the `scans` row?
4. Do we want to expose recent events immediately in the first progress endpoint version, or add them only after the UI for active files ships?
5. Is SSE worth the operational complexity, or is 1s polling enough for the current product stage?

## Recommended First PR

Keep the first PR focused on the core user-visible win:

1. expand `ProcessingStatus` to include `queued` and `running`
2. persist file lifecycle transitions incrementally in the worker
3. add `started_at` / `completed_at` to `scan_files`
4. add `GET /api/scans/{scan_id}/progress` with summary counts
5. update `ScanDetailPage.tsx` to use the summary endpoint for the progress card

That should make the bar materially smoother before richer Stage 2 detail or event feeds are added.
