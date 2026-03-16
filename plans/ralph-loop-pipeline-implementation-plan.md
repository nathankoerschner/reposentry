# Ralph-Loop Pipeline Implementation Plan

Date: 2026-03-15
Source: `research/ralph-loop-pipeline-synthesis.md`

## Goal

Refactor the current Stage 2 scanner loop in `worker/app/scanner/pipeline.py` from a single-agent iterative investigation into a structured multi-role Ralph-loop with three roles:

- **Investigator**: proposes the current best hypothesis and requests evidence
- **Challenger**: tries to falsify or narrow that hypothesis
- **Arbiter**: decides from evidence whether the case is proven, disproven, or still uncertain

Keep the existing two-stage architecture and file-level concurrency model intact.

## Current Code Touchpoints

Primary files:
- `worker/app/scanner/pipeline.py`
- `worker/app/scanner/prompts.py`
- `worker/app/scanner/llm_client.py`

Related models/services likely affected:
- `worker/app/models/enums.py`
- `worker/app/models/scan_file.py`
- `worker/app/models/finding_occurrence.py`
- `worker/app/services/finding_persistence.py`

## Implementation Principles

1. **Do not replace Stage 1** unless needed for instrumentation.
2. **Refactor Stage 2 incrementally** behind clear helper boundaries.
3. **Use structured investigation state**, not only freeform history.
4. **Emit uncertain outcomes explicitly** instead of silently dropping iteration-cap cases.
5. **Require exact repo file + exact line** before emitting definitive issues.
6. **Preserve existing concurrency**, but keep each file investigation internally sequential.

---

## Phase 0: Baseline and Safety Rails

### Deliverables
- Capture current Stage 2 behavior on a few known repos / fixtures
- Add logging needed to compare old vs new loop behavior

### Tasks
- Add structured logging around Stage 2 iterations in `pipeline.py`:
  - iteration count
  - requested context count
  - final verdict
  - parse failures
- Add optional metadata support in `llm_client.py`:
  - `role_name`
  - optional `model`
  - optional `temperature`
- Keep defaults identical to current behavior so this change is low-risk.

### Success criteria
- Existing scans still run unchanged
- Logs clearly show per-file Stage 2 outcomes

---

## Phase 1: Introduce Shared Stage 2 State

### Deliverables
- New typed state containers for multi-round investigations
- Stage 2 orchestration no longer depends on ad hoc `history: list[str]`

### Tasks
Create Stage 2 state types in `worker/app/scanner/pipeline.py` initially (can later move to `worker/app/scanner/evidence.py` or a new module):

- `InvestigationState`
- `EvidenceItem`
- `RoundRecord`
- `RoleOutput` types or normalized dict parsers

Recommended fields:
- suspicious file path
- current hypothesis
- counter hypothesis
- specificity (`file`, `function`, `line`, `line_and_library`)
- candidate sink lines
- candidate sanitizers
- unresolved blockers
- external libraries touched
- evidence items
- round history
- investigator/challenger/arbiter confidence/status

### Refactor tasks
- Convert current `ContextSnippet` output into a more evidence-oriented structure
- Add helpers:
  - `_format_investigation_state(...)`
  - `_append_round_record(...)`
  - `_merge_evidence(...)`
  - `_truncate_evidence_for_prompt(...)`

### Success criteria
- Existing Stage 2 can still run with the new state object even before roles are fully split
- All gathered snippets are represented as structured evidence

---

## Phase 2: Split Prompts and Role Schemas

### Deliverables
- Separate prompt/schema definitions for Investigator, Challenger, and Arbiter

### Tasks
Replace the single Stage 2 prompt in `worker/app/scanner/prompts.py` with role-specific prompt constants and templates:

- `INVESTIGATOR_SYSTEM_PROMPT`
- `INVESTIGATOR_USER_TEMPLATE`
- `CHALLENGER_SYSTEM_PROMPT`
- `CHALLENGER_USER_TEMPLATE`
- `ARBITER_SYSTEM_PROMPT`
- `ARBITER_USER_TEMPLATE`

Also define role-specific output expectations.

#### Investigator output
Should include:
- `hypothesis`
- `candidate_terminal_sites[]`
- `specificity`
- `requests[]`
- `confidence`
- `known_unknowns[]`
- `falsification_conditions[]`

#### Challenger output
Should include:
- `outcome`: `refuted | conceded | needs_context`
- `counter_hypothesis`
- `rebuttals[]`
- `requests[]`
- `remaining_concerns[]`
- `narrowing_hint`

#### Arbiter output
Should include:
- `verdict`: `definitive_issue | definitive_no_issue | continue`
- `confidence`
- `exact_file_path`
- `exact_line_number`
- `proof_chain[]`
- `missing_requirements[]`
- `finding` payload when verdict is `definitive_issue`

### Success criteria
- Prompts are isolated by role
- Schemas are narrow enough to validate robustly
- Old `STAGE2_*` prompt can remain temporarily during rollout if desired

---

## Phase 3: Refactor Stage 2 Orchestration into Role Steps

### Deliverables
- New Stage 2 control flow in `pipeline.py`

### Tasks
Refactor `_run_stage2(...)` into the following helpers:

- `_run_investigator_step(...)`
- `_run_challenger_step(...)`
- `_run_arbiter_step(...)`
- `_resolve_requests(...)`
- `_advance_investigation_state(...)`

### Proposed control flow
1. Seed `InvestigationState` from suspicious file
2. Run Investigator
3. Resolve investigator requests
4. If hypothesis is concrete enough, run Challenger
5. Resolve challenger requests if any
6. Run Arbiter
7. If arbiter says `continue`, iterate
8. On max rounds, emit `uncertain`

### Important behavior changes
- Challenger should only run once there is a concrete enough hypothesis to challenge
- Arbiter should be strict and evidence-bound
- Backtracking is allowed if new evidence invalidates the current branch

### Success criteria
- `_run_stage2(...)` becomes a small orchestrator
- Each role step is testable in isolation
- Final outcomes are one of:
  - `definitive_issue`
  - `definitive_no_issue`
  - `uncertain`

---

## Phase 4: Add Explicit Uncertain Outcome Handling

### Deliverables
- Iteration-cap cases are preserved as structured uncertainty

### Tasks
The research decision says unresolved cases should be emitted as `uncertain` findings rather than discarded.

Implement this in two parts:

#### 1. Pipeline result handling
Update `Stage2Outcome` / verdict handling in `pipeline.py`:
- replace `iteration_cap_reached` as the external terminal state with `uncertain`
- preserve explanation, blockers, and partial evidence summary

#### 2. Persistence/data model review
Decide whether uncertainty should be persisted as:
- a new finding severity/status pattern, or
- scan-file metadata only, or
- a separate uncertain-finding model

Recommended first implementation:
- keep definitive findings in `finding_occurrences`
- record uncertain investigations on `scan_files.error_message` or a new structured field
- only persist `finding_occurrences` for `definitive_issue`

If product requirements want uncertain findings in the UI, add model support deliberately rather than overloading current occurrence rows.

### Files likely affected
- `worker/app/scanner/pipeline.py`
- `worker/app/models/enums.py` (only if adding new statuses)
- `worker/app/models/scan_file.py`
- `worker/app/services/finding_persistence.py`

### Success criteria
- No unresolved investigation is silently lost
- Definitive issues remain clearly separated from uncertain cases

---

## Phase 5: Strengthen Deterministic Evidence Gathering

### Deliverables
- Better repo-grounded context resolution than regex-only search

### Tasks
Create a new module, recommended path:
- `worker/app/scanner/evidence.py`

Move and expand current helpers from `pipeline.py`:
- `_search_symbol_definitions`
- `_search_symbol_usages`
- `_load_file_context`

Add higher-value resolvers:
- AST-based import resolution
- alias tracking
- class/method lookup
- decorator-aware resolution
- wrapper tracing
- call-site resolution
- symbol provenance tracking

### Recommended request kinds
Expand request handling gradually beyond:
- `symbol_definition`
- `symbol_usage`
- `file`

Potential additions:
- `import_resolution`
- `call_chain`
- `class_method_definition`
- `dependency_manifest`

### Success criteria
- Investigator/Challenger can request deeper context without relying on vague repo-wide snippets
- Exact line attribution improves for wrapper-heavy code paths

---

## Phase 6: Dependency Grounding for External API Claims

### Deliverables
- Evidence collection for dependency manifests and package context

### Tasks
Add deterministic loaders for:
- `requirements.txt`
- `requirements/*.txt`
- `pyproject.toml`
- `poetry.lock`

Use this data to:
- list imported third-party package names in suspicious paths
- ground claims about external APIs
- avoid unsupported claims about third-party source lines

### Rule to enforce
The scanner may claim:
- exact repo file
- exact repo line
- exact API/library call at that line

It should **not** claim exact third-party source behavior unless the source/version is actually available and analyzed.

### Success criteria
- External library references in findings are grounded by repo evidence
- Hallucinated package-level reasoning is reduced

---

## Phase 7: Validation, Parsing, and Observability Improvements

### Deliverables
- Role-aware validation and debug traces

### Tasks
In `llm_client.py`:
- support `role_name` for logs
- support optional per-role model selection
- support optional per-role temperature
- record prompt/response metadata for debugging

In `pipeline.py`:
- add parsers/validators for each role’s schema
- reject malformed or speculative arbiter outputs
- log why a case ended as `continue` / `uncertain`

### Success criteria
- Debugging a bad verdict is possible from logs/state
- Parse failures are attributable to a specific role

---

## Phase 8: Tests

### Deliverables
- Unit tests for orchestration, parsing, and evidence resolution

### Test areas

#### Prompt/result parsing
- investigator JSON parsing
- challenger JSON parsing
- arbiter JSON parsing
- malformed output repair path

#### Stage 2 orchestration
- investigator requests context, challenger concedes, arbiter confirms issue
- investigator hypothesis is refuted by challenger
- arbiter returns `continue` until max rounds then `uncertain`
- backtracking path after new evidence

#### Evidence resolution
- symbol definition lookup
- alias/import resolution
- file loading
- deduplication and truncation behavior

#### Persistence behavior
- definitive issues are persisted
- uncertain outcomes are preserved but not persisted as definitive findings

### Suggested placement
- `worker/tests/scanner/test_pipeline.py`
- `worker/tests/scanner/test_evidence.py`
- `worker/tests/scanner/test_llm_client.py`

---

## Recommended Rollout Order

Because rollout strategy is still open, the safest implementation order is:

1. **Phase 0–2**: add state, role prompts, and llm client support
2. **Phase 3**: wire full multi-role orchestration behind a feature flag
3. **Phase 4**: preserve uncertain outcomes
4. **Phase 5–6**: upgrade evidence gathering and dependency grounding
5. **Phase 7–8**: harden validation and tests

If a smaller experiment is preferred, do this MVP first:
- keep most of current Stage 2 loop
- add **Challenger** before final issue emission
- only later add full Arbiter/state refactor

---

## Concrete File-by-File Plan

### `worker/app/scanner/pipeline.py`
- add investigation state dataclasses
- split Stage 2 into role helpers
- replace freeform history with structured round history
- preserve exact-line requirement for definitive findings
- emit `uncertain` terminal outcome

### `worker/app/scanner/prompts.py`
- add role-specific prompts/templates
- retain Stage 1 as-is
- optionally keep legacy Stage 2 prompt during migration

### `worker/app/scanner/llm_client.py`
- add role/model/temperature overrides
- improve logging/traceability

### `worker/app/scanner/evidence.py` (new)
- move current search/load helpers here
- add AST/import-aware resolution
- add dependency manifest loading

### `worker/app/services/finding_persistence.py`
- ensure only definitive issues are persisted as findings
- preserve uncertain case handling separately

### `worker/app/models/*`
- only extend models if product needs first-class uncertain investigations in storage/UI

---

## Acceptance Criteria

The implementation is complete when:

1. Stage 1 behavior remains stable
2. Stage 2 uses Investigator, Challenger, and Arbiter roles
3. The loop passes structured evidence state between rounds
4. Definitive issues require exact repo file + exact line
5. Unresolved cases are surfaced as `uncertain`
6. Evidence gathering is stronger than regex-only lookup for key cases
7. Logs/tests make each verdict explainable

---

## Open Questions To Resolve During Implementation

1. Should `uncertain` become a first-class persisted model/UI object or remain scan-file metadata initially?
2. Should the first rollout be feature-flagged per repo / environment?
3. Do we want per-role model tiering immediately, or after correctness is validated?
4. Should evidence tooling live in `pipeline.py` temporarily or move directly into `evidence.py`?
5. What evaluation corpus will be used to compare false positives, line accuracy, and unresolved rates before/after the refactor?

## Recommended First PR

Keep the first PR small and high leverage:

1. add role-aware `call_llm_json(...)` options in `llm_client.py`
2. add structured investigation state types in `pipeline.py`
3. split prompts in `prompts.py`
4. refactor `_run_stage2(...)` into role helper skeletons without changing evidence tooling yet

That establishes the architecture cleanly before deeper evidence-resolution work.
