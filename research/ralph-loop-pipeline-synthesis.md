# Ralph-Loop Pipeline Redesign – Synthesized Research

_Date: 2026-03-15_

## Executive Summary

The current RepoSentry scanner already contains the foundation of a Ralph-loop style investigation system: Stage 1 performs high-recall triage, and Stage 2 runs an iterative, context-gathering investigation loop over suspicious files. However, the current Stage 2 is still a **single-agent self-loop**. It lacks adversarial challenge, structured certainty criteria, explicit evidence-state modeling, and robust grounding for exact line- and library-level attribution.

A strong redesign keeps the existing two-stage pipeline shape but refactors Stage 2 into a **multi-role iterative evidence loop** built around:

- **Investigator / Hypothesizer**: proposes the current best issue hypothesis and requests missing evidence
- **Challenger / Falsifier**: tries to disprove or narrow the hypothesis
- **Arbiter / Precision Verifier**: decides only from gathered evidence whether the issue is proven, disproven, or still unresolved

The key design principle is to pass forward a **structured evidence dossier**, not just freeform conversational history. This reduces anchoring, improves falsifiability, and makes “exact file + exact line + exact API/library” conclusions more defensible.

---

## 1. Current Pipeline

### Stage 1: Triage Classifier

Current behavior:
- Single-shot LLM classification per Python file
- Output: `suspicious` or `not_suspicious`
- High recall by design; ambiguous files are treated as suspicious
- No repo-wide reasoning beyond the file itself

Relevant behavior from the current implementation:
- Parse failure marks file as failed
- Stage 1 is intentionally broad and low-precision

### Stage 2: Iterative Investigation

Current behavior:
- Runs only on files classified as suspicious
- Up to 10 investigation iterations per file
- Each iteration can request additional context:
  - symbol definitions
  - symbol usages
  - file contents
- Context is gathered deterministically through repo search helpers and fed back into the next call
- Ends when the model returns:
  - `definitive_issue`
  - `definitive_no_issue`
  - `iteration_cap_reached`

Useful existing properties:
- Iterative evidence gathering already exists
- Repo-aware context resolution already exists
- Async/concurrent file processing already exists
- Current Stage 2 is a good base to evolve rather than replace entirely

---

## 2. Why the Current Loop Is Not Yet a True Ralph Loop

Both research documents agree that Stage 2 is useful but incomplete.

### 2.1 Anchoring bias

Although each LLM call is fresh, the model is fed prior history and prior summaries. This means later steps are often anchored to earlier hypotheses rather than independently challenging them.

### 2.2 No adversarial role separation

Today the same reasoning thread proposes, updates, and effectively judges its own conclusions. There is no dedicated mechanism for falsification.

### 2.3 No structured certainty model

The current schema lacks explicit fields for:
- confidence
- unresolved blockers
- evidence completeness
- exact line attribution status
- exact library/API attribution status

As a result, “certainty” is mostly prompt-driven rather than enforced in state.

### 2.4 Evidence gathering is too shallow for exact attribution

Regex-style symbol/file lookup is often not enough for:
- imported aliases
- re-exports
- wrappers
- decorators
- indirect call chains
- method resolution
- framework callback wiring

### 2.5 External library attribution is weakly grounded

The current pipeline can mention third-party APIs, but it does not systematically gather:
- dependency manifests
- lockfiles
- package versions
- imported package inventory
- library source or docs

This makes “exact library causing the issue” less reliable unless carefully constrained.

### 2.6 Capped investigations may lose useful signal

One document notes that iteration-cap cases are currently discarded entirely. That is conservative, but may also throw away partially useful findings or uncertainty traces.

---

## 3. Ralph-Loop Target Architecture

### 3.1 Core Idea

For each suspicious file, maintain a shared investigation state and run multiple fresh LLM roles against that shared evidence.

### 3.2 Recommended Roles

#### Role A: Investigator / Hypothesizer
Responsibilities:
- identify the most plausible risk path
- propose candidate sink lines
- request targeted evidence
- explain what would falsify the current hypothesis
- progressively narrow from file-level suspicion toward exact line/API attribution

Suggested outputs:
- `hypothesis`
- `candidate_terminal_sites[]`
- `specificity`
- `requests[]`
- `confidence`
- `known_unknowns[]`
- `falsification_conditions[]`

#### Role B: Challenger / Falsifier
Responsibilities:
- attempt to disprove the current hypothesis
- look for sanitizers, validators, wrappers, dead paths, or safe framework behavior
- request evidence specifically aimed at falsification
- either refute, request more context, or concede

Suggested outputs:
- `outcome`: `refuted` | `conceded` | `needs_context`
- `counter_hypothesis`
- `rebuttals[]`
- `requests[]`
- `remaining_concerns[]`
- `narrowing_hint`

#### Role C: Arbiter / Precision Verifier
Responsibilities:
- decide strictly from gathered evidence
- reject speculation
- require exact file/line/API evidence for final attribution
- determine whether the case is proven, disproven, or still unresolved

Suggested outputs:
- `verdict`: `definitive_issue` | `definitive_no_issue` | `continue`
- `confidence`
- `exact_file_path`
- `exact_line_number`
- `exact_library_or_api`
- `proof_chain[]`
- `missing_requirements[]`

---

## 4. Proposed Loop Flow

### MVP loop

1. Investigator analyzes suspicious file and current evidence
2. System resolves requested context deterministically
3. If the hypothesis is concrete enough, run Challenger
4. Resolve challenger-requested context if needed
5. Run Arbiter
6. Arbiter returns one of:
   - `definitive_issue`
   - `definitive_no_issue`
   - `continue`
7. Repeat until convergence or iteration cap

### Convergence criteria

A **definitive issue** should require:
- exact repo file path
- exact line number
- exact sink/API/library call at that line
- realistic exploit or data-flow path
- no unresolved sanitizer/blocker that would materially change the conclusion
- arbiter confidence above threshold

A **definitive no issue** should require:
- suspicious pattern is explained with concrete evidence
- relevant sanitization/validation/wrapper behavior is actually located and understood
- no material challenger concerns remain unresolved

A **continue** result should be returned when:
- sink line is still uncertain
- sanitizer behavior is unknown
- imported library behavior is not grounded
- attribution remains speculative

---

## 5. Investigation State and Data Modeling

A major synthesis point across both documents is that the current freeform Stage 2 history should become structured state.

### Recommended state objects

```python
@dataclass
class InvestigationState:
    suspicious_file_path: str
    current_hypothesis: str | None
    counter_hypothesis: str | None
    specificity: Literal["file", "function", "line", "line_and_library"]
    evidence_items: list[EvidenceItem]
    candidate_sink_lines: list[int]
    candidate_sanitizers: list[str]
    import_chain: list[str]
    symbol_resolution_chain: list[str]
    external_libraries_touched: list[str]
    unresolved_blockers: list[str]
    investigator_confidence: int | None
    challenger_status: Literal["refuted", "conceded", "needs_context"] | None
    arbiter_status: Literal["issue", "no_issue", "continue"] | None
    round_history: list[RoundRecord]

@dataclass
class EvidenceItem:
    kind: str
    file_path: str
    line_start: int | None
    line_end: int | None
    summary: str
    snippet: str

@dataclass
class RoundRecord:
    round_number: int
    investigator_summary: str
    challenger_summary: str | None
    arbiter_summary: str | None
    context_requests: list[dict]
    context_resolved: list[EvidenceItem]
```

### Monotonic narrowing

The hypothesis should become more precise over time. A useful specificity ladder is:
- `file`
- `function`
- `line`
- `line_and_library`

The system should reject regressions unless evidence explicitly invalidates a prior narrower hypothesis.

---

## 6. Evidence Gathering Upgrades

Both documents agree the redesign is not just prompt work. Better tooling is required.

### Reuse existing context resolvers

Keep and reuse current helpers for:
- symbol definitions
- symbol usages
- file loading

### Add deeper code-navigation support

Recommended additions:
- AST-based import resolution
- alias tracking
- class and method definition lookup
- wrapper tracing
- decorator-aware search
- call-site resolution
- symbol provenance tracking

Possible home for this logic:
- `worker/app/scanner/evidence.py`

This is one of the highest-value non-prompt improvements if exact attribution is the goal.

### External dependency grounding

To make API/library attribution credible, gather:
- `requirements.txt`
- `pyproject.toml`
- `poetry.lock`
- `requirements/*.txt`
- imported package names from suspicious and related files
- optionally installed package metadata if available in the worker environment

Practical recommendation:
- require exact **repo file + repo line + external API/library call**
- only claim exact third-party source line if source and version are actually available and analyzed

---

## 7. Prompt Strategy

### Investigator prompt
Should require:
- current best hypothesis
- candidate terminal lines
- missing evidence requests
- specificity level
- explicit reasons the hypothesis may be wrong

### Challenger prompt
Should require:
- strongest concrete rebuttal
- safe explanations
- evidence requests specifically aimed at falsification
- concession if no grounded refutation is found

### Arbiter prompt
Should require:
- decision only from gathered evidence
- exact culprit file/line/API if proven
- proof chain
- explicit blockers when not proven
- prohibition against inventing missing evidence

---

## 8. Implementation Delta

### Components that can remain largely unchanged
- file discovery
- Stage 1 triage shape
- async concurrency model
- base LLM JSON call helper
- current deterministic context resolution as a foundation

### Components that should change

#### `pipeline.py`
Refactor current `_run_stage2(...)` into role-specific helpers:
- `_run_investigator_step(...)`
- `_run_challenger_step(...)`
- `_run_arbiter_step(...)`
- `_resolve_requests(...)`
- `_advance_investigation_state(...)`

Keep `_run_stage2(...)` or equivalent as top-level orchestration.

#### `prompts.py`
Expand from the current two-prompt model to separate prompts and schemas for:
- Stage 1
- Investigator
- Challenger
- Arbiter

#### `llm_client.py`
Recommended enhancements:
- optional model override
- optional temperature override
- role name for logging/traceability
- prompt/response metadata for debugging

This enables role-appropriate model selection.

---

## 9. Model Strategy

A useful role-based model strategy:

| Role | Priority | Suggested model strategy |
|------|----------|--------------------------|
| Stage 1 | speed, recall | cheap/fast model |
| Investigator | breadth, pattern spotting | medium/high-quality model |
| Challenger | rigor, careful reasoning | medium/high-quality model |
| Arbiter | precision, confidence calibration | strongest/conservative model |

This can improve quality while containing cost by reserving the most expensive model for final verification.

---

## 10. Cost, Latency, and Operational Tradeoffs

### Expected benefits
- lower false-positive risk through adversarial challenge
- more reliable exact line/API attribution
- better auditability through structured proof chains
- more debuggable failure modes

### Costs and tradeoffs
- more LLM calls per suspicious file
- more sequential per-file latency
- more prompt/schema maintenance
- more engineering complexity in orchestration and evidence tooling

### Mitigations
- only run Challenger once a concrete candidate exists
- only run Arbiter when evidence seems plausibly sufficient
- keep evidence snippets tightly targeted
- preserve cross-file concurrency
- cap rounds aggressively
- cache symbol and context resolution

---

## 11. Migration Path

### Phase 1: Baseline measurement
Measure current pipeline on known-vulnerable and known-safe repos:
- true positives
- false positives
- false negatives
- line-number accuracy
- rate of iteration-cap cases

### Phase 2: Add Challenger first
Smallest high-value step:
- keep most of current Stage 2
- add Challenger as a falsification pass before emitting findings
- drop or downgrade findings the challenger refutes

This is likely the best first experiment.

### Phase 3: Full multi-role Stage 2
Add:
- structured investigation state
- Arbiter/Verifier
- explicit stop criteria and confidence thresholds

### Phase 4: Evidence tooling upgrades
Add AST/import-aware navigation and dependency grounding.

### Phase 5: Model tiering and trace persistence
Optimize cost/quality and improve debugging by persisting:
- role outputs per round
- evidence requests and resolutions
- final proof chain
- reasons for uncertainty or iteration-cap termination

---

## 12. Final Recommendation

The strongest synthesized recommendation is:

1. **Keep the existing two-stage architecture**
2. **Redesign Stage 2 into a multi-role Ralph-loop evidence process**
3. **Pass structured investigation state, not just conversational history**
4. **Add a Challenger first, since adversarial falsification is the highest-value incremental gain**
5. **Require Arbiter-backed exact file/line/API proof before emitting a definitive issue**
6. **Strengthen deterministic evidence gathering, especially import and wrapper resolution**
7. **Constrain library attribution to what is actually grounded by repo and dependency evidence**

In short: the current system is already close enough that this should be treated as a **Stage 2 refactor**, not a from-scratch redesign.

---

## 13. Resolved Decisions

Based on follow-up answers, the design decisions are now:

1. **Role C**
   - Role C should be an **Arbiter**
   - Its primary responsibility is to decide `definitive_issue`, `definitive_no_issue`, or `continue` from the gathered evidence
   - Precision requirements still matter, but they are subordinate to the arbiter role

2. **Iteration-cap / unresolved cases**
   - Unresolved cases should be **emitted as `uncertain` findings**
   - They should not be treated as definitive issues, but the system should preserve and surface the uncertainty state rather than discarding it

3. **Rollout strategy**
   - **Not decided yet**
   - The document intentionally leaves open whether to do an incremental rollout or a full Stage 2 redesign first

4. **Scope of exact attribution**
   - **Exact line is sufficient**
   - The system should focus on exact repo-line attribution rather than requiring deeper third-party source attribution

5. **Specificity progression**
   - The investigation should be allowed to **backtrack** when new evidence invalidates the current branch
   - Progress should generally trend toward greater specificity, but it should not be enforced as strictly monotonic

---

## Current Decision Defaults

The current agreed defaults are:
- Role C = **Arbiter**
- Iteration-cap cases = **emit `uncertain` findings**
- Rollout strategy = **undecided**
- “Exact attribution” = **exact repo line is enough**
- Specificity progression = **backtracking allowed when evidence requires it**
