# LLM Pipeline Research: Evolving to a Ralph-Loop Style Investigation Pipeline

## Summary

The current worker scanner already has an iterative LLM pipeline, but it is **not yet a true Ralph-loop style system**.

Today it works like this:

1. **Stage 1** classifies each Python file as `suspicious` or `not_suspicious`.
2. **Stage 2** iteratively investigates suspicious files by:
   - sending the suspicious file, repo index, prior history, and gathered snippets to the LLM
   - allowing the LLM to request more context
   - resolving those requests with repo searches
   - looping until a final verdict or iteration cap

This is already a useful iterative investigator, but it is still effectively **one reasoning thread** carried across iterations, not a sequence of fresh or adversarial LLM passes over a shared evidence state.

---

## Relevant Code

- `worker/app/scanner/pipeline.py`
  - `_run_stage1(...)` at line ~113
  - `_run_stage2(...)` at line ~381
  - `_process_file(...)` at line ~433
  - `run_scan_pipeline(...)` at line ~525
- `worker/app/scanner/prompts.py`
  - `STAGE1_SYSTEM_PROMPT`
  - `STAGE2_SYSTEM_PROMPT`
  - `MAX_STAGE2_INVESTIGATION_ITERATIONS = 10`
- `worker/app/scanner/llm_client.py`
  - `call_llm_json(...)`
- `worker/app/config.py`
  - `openai_model = "gpt-4o"`
  - `max_file_retries = 2`
  - `max_concurrent_files = 5`
- `worker/tests/test_scanner_pipeline.py`
  - covers stage 1, stage 2 looping, parse failures, and integration behavior

---

## Current Pipeline Behavior

### Stage 1

Stage 1 is a coarse suspiciousness classifier.

It asks the model to output:

```json
{
  "classification": "suspicious" | "not_suspicious",
  "reason": "..."
}
```

Behavioral notes:
- high recall by design
- ambiguous files are treated as suspicious
- parse failure marks file as failed

### Stage 2

Stage 2 is a repo-aware iterative security investigator.

The stage 2 prompt supports:
- `continue`
- `final`
- final verdicts of:
  - `definitive_issue`
  - `definitive_no_issue`
  - `iteration_cap_reached`

The model can request additional context via:
- `symbol_definition`
- `symbol_usage`
- `file`

The system resolves those requests through helper functions such as:
- `_search_symbol_definitions(...)`
- `_search_symbol_usages(...)`
- `_load_file_context(...)`
- `_resolve_stage2_requests(...)`

This makes stage 2 a **single iterative investigation loop**.

---

## Why This Is Not Yet a Ralph-Loop Style Pipeline

If the target is:

> iteratively pass the response to a new LLM instance until one LLM is certain of the exact line or library causing the issue

then the current design falls short in several ways.

### 1. Anchoring bias across iterations

Although each OpenAI call is fresh, the model is fed:
- prior investigation history
- prior gathered snippets
- its previous summaries

That means later iterations are strongly anchored to earlier hypotheses.

### 2. No structured certainty model

The current schema has verdicts, but not explicit fields for:
- confidence
- evidence completeness
- unresolved blockers
- exactness of line attribution
- exactness of library attribution

So “certainty” exists only as a prompt instruction, not a system-enforced state.

### 3. Shallow code navigation

Evidence gathering is based on repo file listing and regex-ish searches. That is often not enough for exact line attribution when code involves:
- imported aliases
- re-exports
- class methods
- wrappers
- decorators
- indirect call chains
- framework callback registration

### 4. Weak grounding for external library blame

The prompt says terminal decisions can rely on stdlib or external packages, but the system does not currently gather:
- dependency manifests
- lockfiles
- package versions
- library source
- API documentation

So “exact library causing the issue” is not truly grounded today.

### 5. No adversarial second opinion

There is no separate reviewer whose job is to:
- disprove the finding
- locate sanitization
- identify safe wrappers
- challenge overconfident conclusions

This increases false-positive and false-exactness risk.

---

## Recommended Redesign

Instead of naively chaining raw responses from one model into the next, the better design is:

> a structured evidence dossier plus multiple fresh LLM roles

### Core Idea

For each suspicious file:

1. **Investigator** proposes a likely issue and requests targeted evidence.
2. The system gathers evidence deterministically.
3. **Challenger** reviews the evidence and tries to disprove, narrow, or redirect the claim.
4. The system gathers more evidence if needed.
5. **Arbiter** decides whether the evidence is sufficient for:
   - definitive issue
   - definitive no issue
   - insufficient evidence / continue
6. Repeat until exact attribution is achieved or the loop cap is reached.

This preserves the useful iterative behavior already present in stage 2, while reducing anchoring and forcing explicit falsification.

---

## Proposed Ralph-Loop Style Roles

### 1. Investigator

Responsibilities:
- identify most plausible risk path
- nominate candidate sink lines
- request the next missing evidence
- explain what would falsify the hypothesis

Suggested structured output:
- `hypothesis`
- `candidate_terminal_sites[]`
- `requests[]`
- `confidence`
- `known_unknowns[]`

### 2. Challenger

Responsibilities:
- attack the current hypothesis
- search for sanitizers, validators, wrappers, dead paths
- identify why the proposed sink may be safe
- request evidence specifically aimed at falsification

Suggested structured output:
- `counter_hypothesis`
- `rebuttals[]`
- `requests[]`
- `remaining_concerns[]`

### 3. Arbiter

Responsibilities:
- decide only from gathered evidence
- reject speculative claims
- require exact line/library evidence for final attribution

Suggested structured output:
- `verdict`
- `confidence`
- `exact_file_path`
- `exact_line_number`
- `exact_library_or_api`
- `proof_chain[]`
- `missing_requirements[]`

---

## Required System Changes

### 1. Replace unstructured stage-2 history with structured investigation state

Current stage 2 tracks:
- `history: list[str]`
- `gathered_context: list[ContextSnippet]`

This is too loose for a multi-role system.

Recommended new dataclasses:
- `InvestigationState`
- `EvidenceItem`
- `CandidateConclusion`
- possibly `TerminalAttribution`

Suggested fields:
- suspicious file path
- current hypothesis
- counter-hypothesis
- evidence snippets
- symbol resolution chain
- import chain
- candidate sink lines
- candidate sanitizers
- external libraries touched
- unresolved blockers
- per-role confidence
- arbiter status

### 2. Split `_run_stage2(...)` into role-specific helpers

Current orchestrator:
- `worker/app/scanner/pipeline.py::_run_stage2`

Recommended split:
- `_run_investigator_step(...)`
- `_run_challenger_step(...)`
- `_run_arbiter_step(...)`
- `_resolve_requests(...)`
- `_advance_investigation_state(...)`

Keep `_run_stage2(...)` as the top-level coordinator.

### 3. Improve evidence gathering beyond regex

High-value additions:
- AST-based import resolution
- alias tracking
- class and method definition lookup
- call-site resolution
- wrapper tracing
- decorator-aware search
- symbol provenance tracking

A likely new module:
- `worker/app/scanner/evidence.py`

This is likely the most important non-prompt upgrade if the goal is exact line attribution.

### 4. Add external library grounding

To support exact library attribution, gather:
- `requirements.txt`
- `pyproject.toml`
- `poetry.lock`
- `requirements/*.txt`
- imported package names in suspicious files and evidence files
- optionally installed package metadata if available in the worker environment

Practical target:
- identify the **exact external API/library call** at the repo line
- do not claim exact third-party source line unless the library source is actually available and analyzed

### 5. Add explicit stop criteria

#### Definitive issue should require
- exact repo file path
- exact line number
- exact sink/API/library call
- realistic exploit path
- no unresolved sanitizer/blocker that would materially change the result
- arbiter confidence above threshold

#### Definitive no issue should require
- the suspicious pattern is explained away with concrete evidence
- any relevant sanitizer/validator implementation is located and understood
- challenger found no bypass that remains unresolved

#### Continue should be used when
- sink line is not yet proven
- sanitizer behavior remains unknown
- library behavior is not grounded
- attribution is still speculative

### 6. Extend the LLM client for multi-role use

Current `call_llm_json(...)` always uses one configured model.

Recommended enhancements:
- optional `model` override
- optional `temperature` override
- role name for logging/traceability
- prompt/response metadata for debugging

Potential strategy:
- investigator: cheaper model or moderate cost model
- challenger: same or slightly stronger
- arbiter: strongest, most conservative model

### 7. Persist investigation traces

This will be important for debugging and trust.

Persist or log:
- each role output per iteration
- evidence requests and what was resolved
- final proof chain
- why the arbiter finalized
- uncertainty reasons on capped cases

---

## Recommended Prompt Strategy

### Investigator prompt

Should ask for:
- current best hypothesis
- candidate terminal lines
- missing evidence requests
- explicit reasons the hypothesis may be wrong

### Challenger prompt

Should ask for:
- strongest rebuttal to the current hypothesis
- safe explanations
- specific missing evidence needed to falsify or narrow the claim

### Arbiter prompt

Should ask for:
- final verdict only from gathered evidence
- exact culprit file/line/library if proven
- proof chain
- blockers if not proven

The arbiter should be forbidden from inventing missing evidence.

---

## Practical Risks and Tradeoffs

### Cost

A multi-role loop can multiply token cost quickly.

Mitigations:
- only run challenger after investigator proposes a concrete candidate sink
- only run arbiter when evidence is plausibly sufficient
- keep evidence snippets highly targeted
- reduce maximum loop count if each cycle now includes multiple model calls

### Latency

The worker currently processes files concurrently (`max_concurrent_files = 5`), but deeper per-file loops will increase wall-clock time.

Mitigations:
- early exit for obvious safe cases
- cache symbol resolution / file context
- cap deep analysis to a subset of suspicious files if needed

### False exactness

This is the main danger.

The system must avoid claiming exact line/library attribution unless the evidence dossier genuinely supports it.

### Third-party line attribution feasibility

Exact third-party source line attribution is usually unrealistic unless you also:
- fetch dependency source
- inspect vendored package code
- pin package versions reliably

A more practical and defensible goal is:
- exact repo file and line
- exact external API/library call responsible for the issue

---

## Recommended MVP

A good first version would keep the current two-stage shape and only redesign stage 2.

### MVP Stage 2 Flow

1. Investigator reviews suspicious file and current evidence.
2. System resolves requested context.
3. If investigator proposes a candidate issue, run Challenger.
4. Resolve challenger-requested context if needed.
5. Run Arbiter.
6. Arbiter returns:
   - definitive issue
   - definitive no issue
   - continue
7. Loop until cap.

This would deliver most of the value of a Ralph-loop style design without requiring a complete rewrite of the worker architecture.

---

## Highest-Value Implementation Order

1. Add structured `InvestigationState`
2. Split stage 2 into investigator / challenger / arbiter helpers
3. Upgrade evidence gathering to AST/import-aware resolution
4. Add confidence and stop criteria
5. Add dependency/library grounding
6. Persist trace/debug metadata

---

## Conclusion

### Answer to the research question

Yes, the current LLM pipeline can be evolved into a Ralph-loop style pipeline.

However, the best implementation is **not** a naive chain of raw responses between LLM instances. That approach would amplify anchoring, cost, and hallucinated precision.

The better design is:

- a shared structured evidence dossier
- fresh LLM roles per iteration
- deterministic evidence gathering between steps
- explicit arbiter stop criteria for exact line or exact API/library attribution

### Short recommendation

Refactor stage 2 into a **multi-role iterative evidence loop**:
- Investigator
- Challenger
- Arbiter

Use the existing request-resolution mechanism as the foundation, but strengthen it with structured state and better code navigation.

That would make the pipeline much closer to the “iterate until one LLM is certain of the exact line or library causing the issue” goal while staying testable and grounded.
