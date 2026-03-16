# Ralph Loop Pipeline Redesign – Research Document

## Date: 2025-03-15

---

## 1. Current Pipeline Summary

The RepoSentry scanner uses a **two-stage LLM pipeline**:

### Stage 1 – Triage Classifier (single-shot)
- Each Python file is sent to a single LLM call
- Binary output: `suspicious` / `not_suspicious`
- High-recall, low-precision by design ("when in doubt, suspicious")
- No iteration, no cross-file awareness

### Stage 2 – Iterative Investigation (already partially looping)
- Only runs on files flagged `suspicious` by Stage 1
- Runs up to **10 iterations** of LLM calls per suspicious file
- Each iteration can request additional context: symbol definitions, symbol usages, or full file contents
- Context is resolved by grepping the repo and appended to the next iteration's prompt
- Terminates when the LLM declares `definitive_issue`, `definitive_no_issue`, or hits the iteration cap

### Key Observations
- Stage 2 already has a loop, but it's a **single-agent self-loop** – the same LLM instance refines its own hypothesis
- There is no **adversarial challenge**, **independent verification**, or **role specialization**
- The LLM can anchor on its initial hypothesis and never escape it
- When the iteration cap is hit, findings are discarded entirely (conservative but wasteful)
- Line-level precision is requested but not verified or challenged

---

## 2. What is a "Ralph Loop" Style Pipeline?

The Ralph Loop (named after the iterative refinement pattern used in adversarial/debate-style AI systems) is a multi-agent iterative pipeline where:

1. **Multiple distinct LLM "roles"** pass a work product between them
2. Each role has a **specialized mandate** (e.g., hypothesizer, challenger, verifier)
3. The loop continues until a **convergence condition** is met — typically when one agent is certain of the exact root cause (line + library)
4. Each iteration **narrows the scope** rather than re-analyzing from scratch

### Core Principles
| Principle | Description |
|-----------|-------------|
| **Role separation** | Different agents have different system prompts and mandates |
| **Adversarial refinement** | At least one agent's job is to challenge/falsify the current hypothesis |
| **Monotonic narrowing** | Each pass must produce a more specific claim than the last |
| **Convergence criterion** | The loop exits when an agent declares certainty at the line/library level with supporting evidence |
| **Evidence accumulation** | Context gathered in earlier rounds persists and grows |
| **Bounded iteration** | Hard cap prevents runaway cost |

### Analogy
Think of it like a security review meeting:
- **Analyst** proposes: "I think there's an injection risk in `query_builder.py` around line 45"
- **Challenger** responds: "But `sanitize_input()` is called on L42 — show me the implementation"
- **Analyst** (next round): "The sanitizer doesn't handle unicode normalization, see `utils/sanitize.py:18`"
- **Verifier** concludes: "Confirmed — `sanitize_input` on `utils/sanitize.py:18` uses `str.replace` which misses `\uff07` (fullwidth apostrophe). This is the root cause."

---

## 3. Proposed Ralph Loop Architecture for RepoSentry

### 3.1 Agent Roles

#### Agent A: **Hypothesis Generator** (existing Stage 2, modified)
- Receives the suspicious file + repo index
- Produces an initial vulnerability hypothesis with file path, line number, and description
- Can request additional context (symbol defs, usages, files)
- Must output a **specificity level**: `file` → `function` → `line` → `line+library`

#### Agent B: **Challenger / Falsifier**
- Receives Agent A's hypothesis + all accumulated context
- Mandate: **try to disprove** the hypothesis
- Must either:
  - Provide a concrete refutation (e.g., "the input is sanitized at L42 by `bleach.clean()`")
  - Request additional context to attempt refutation
  - Concede: "I cannot refute this — hypothesis stands"
- This is the key innovation: an adversarial check prevents false positives

#### Agent C: **Precision Verifier** (terminal)
- Only invoked when Agent B concedes
- Receives the full evidence chain
- Must pinpoint the **exact line** and **exact library/function** causing the vulnerability
- Must output a confidence score (0-100)
- If confidence < threshold (e.g., 85), kicks back to Agent A with new questions
- If confidence ≥ threshold, the finding is emitted

### 3.2 Loop Flow

```
┌─────────────────────────────────────────────────────────┐
│                    ENTRY (suspicious file)               │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  Agent A: Hypothesizer │◄──────────────┐
              │  - Analyze file        │               │
              │  - Request context     │               │
              │  - Produce hypothesis  │               │
              └───────────┬────────────┘               │
                          │                            │
                          ▼                            │
              ┌────────────────────────┐               │
              │  Agent B: Challenger   │               │
              │  - Try to refute       │               │
              │  - Request context     │               │
              │  - Refute or concede   │               │
              └───────────┬────────────┘               │
                          │                            │
                    ┌─────┴──────┐                     │
                    │            │                     │
                 Refuted      Conceded                 │
                    │            │                     │
                    ▼            ▼                     │
              Back to A   ┌────────────────────┐       │
              (narrower   │ Agent C: Verifier  │       │
               hypothesis)│ - Pinpoint line    │       │
                    │     │ - Confidence score │       │
                    │     └────────┬───────────┘       │
                    │              │                    │
                    │        ┌─────┴──────┐            │
                    │        │            │            │
                    │   Confident    Not confident     │
                    │        │            │            │
                    │        ▼            └────────────┘
                    │   EMIT FINDING
                    │
                    ▼
              (If max iterations reached)
              EMIT "uncertain" or DISCARD
```

### 3.3 Convergence Criterion

The loop terminates with a finding when **all three conditions** are met:
1. Agent A has a hypothesis at `line+library` specificity
2. Agent B cannot refute it (concedes)
3. Agent C has confidence ≥ 85% on the exact line and root cause

### 3.4 Data Structures

```python
@dataclass
class Hypothesis:
    file_path: str
    vulnerability_type: str
    severity: str
    specificity: Literal["file", "function", "line", "line_and_library"]
    line_number: int | None
    function_name: str | None
    root_cause_library: str | None  # e.g., "sqlite3", "pickle", "yaml.load"
    description: str
    evidence: list[str]  # accumulated evidence chain
    
@dataclass
class ChallengeResult:
    outcome: Literal["refuted", "conceded", "needs_context"]
    refutation: str | None           # if refuted, why
    narrowing_hint: str | None       # if refuted, suggestion for Agent A
    context_requests: list[dict]     # if needs_context

@dataclass  
class VerificationResult:
    confidence: int                  # 0-100
    exact_line: int
    exact_file: str
    root_cause: str                  # "pickle.loads() deserializes untrusted input"
    code_snippet: str
    questions: list[str] | None      # if not confident, what more is needed
```

---

## 4. Implementation Delta from Current Code

### What stays the same
- `file_discovery.py` — unchanged
- `llm_client.py` — unchanged (all agents use `call_llm_json`)
- Stage 1 triage — unchanged
- Context resolution (`_search_symbol_definitions`, `_search_symbol_usages`, `_load_file_context`) — reused by all agents
- Async concurrency model — unchanged

### What changes

| Component | Current | Ralph Loop |
|-----------|---------|------------|
| `prompts.py` | 2 system prompts (Stage 1, Stage 2) | 4 system prompts (Stage 1, Hypothesizer, Challenger, Verifier) |
| `pipeline.py::_run_stage2` | Single-agent loop, 10 iterations | Multi-agent loop, ~4-6 rounds (each round = A→B→C) |
| Stage 2 JSON schema | One schema for all iterations | Three schemas (one per agent role) |
| Convergence | LLM self-declares "definitive_issue" | Three-agent consensus with confidence threshold |
| Context management | Linear accumulation | Partitioned by round with evidence chain |
| Finding quality | LLM self-reports line numbers | Adversarially verified line + library |
| Cost per suspicious file | ~2-10 LLM calls | ~6-18 LLM calls (2-3x current) |

### New files needed
- `prompts.py` — expanded with 3 new system prompts + schemas
- `pipeline.py` — new `_run_ralph_loop()` replacing `_run_stage2()`
- No new dependencies required

---

## 5. Prompt Sketches

### Agent A: Hypothesizer System Prompt (sketch)

```
You are the Hypothesis Agent in a multi-agent security investigation loop.

Your job: analyze the suspicious file and propose a specific vulnerability 
hypothesis. Each round, you must be MORE specific than the last.

Specificity levels (you must always advance or maintain):
- "file": something in this file is risky
- "function": the risk is in function X
- "line": the risk is on line N
- "line_and_library": the risk is on line N, caused by library/function Y

If the Challenger refuted your previous hypothesis, incorporate their 
feedback and propose a refined or alternative hypothesis.

If the Verifier had questions, answer them in your next hypothesis.
```

### Agent B: Challenger System Prompt (sketch)

```
You are the Challenger Agent. Your mandate is adversarial: try to DISPROVE 
the Hypothesis Agent's claim.

You receive: the hypothesis, the file, and all gathered context.

You must either:
1. REFUTE: show concrete evidence the code is safe (e.g., input is 
   sanitized, function is never called with user input, etc.)
2. CONCEDE: you cannot find evidence to disprove the hypothesis
3. REQUEST_CONTEXT: you need more information before you can challenge

When refuting, cite specific lines and logic. Vague refutations are not 
accepted. If you cannot find a concrete safety mechanism, you must concede.
```

### Agent C: Verifier System Prompt (sketch)

```
You are the Precision Verifier. You are called only when the Challenger 
has conceded that a vulnerability hypothesis is plausible.

Your job: determine the EXACT line and EXACT root cause (library call, 
function, or language construct) that makes this vulnerability exploitable.

Output your confidence (0-100) that you have correctly identified the 
precise root cause. If confidence < 85, explain what additional information 
would raise your confidence.

A confidence of 85+ means: "I am certain this specific line, using this 
specific library/function, is the root cause, and I can explain the 
attack path."
```

---

## 6. Cost & Latency Analysis

### Current Pipeline (per suspicious file)
- Best case: 2 LLM calls (Stage 1 + 1 Stage 2 iteration)
- Worst case: 11 LLM calls (Stage 1 + 10 Stage 2 iterations)
- Average (estimated): ~5 calls

### Ralph Loop Pipeline (per suspicious file)
- Best case: 4 calls (Stage 1 + A + B-concedes + C-confident)
- Typical: 8-12 calls (Stage 1 + 2-3 rounds of A→B, then C with 1 kickback)
- Worst case: ~18 calls (Stage 1 + 5 full A→B→C rounds hitting cap)
- Average (estimated): ~9 calls

### Cost Impact
- **~1.8x cost increase** per suspicious file on average
- Mitigated by: higher precision means fewer false positives to triage (saving human time)
- Can use cheaper models for Agent A (fast, creative) and Agent B (analytical), reserving the expensive model for Agent C only

### Latency Impact
- Each round is sequential (A must finish before B can challenge)
- But rounds within a single file are typically 3 calls, not 10 small ones
- Net latency is similar or slightly higher
- Cross-file parallelism still applies (multiple files processed concurrently)

---

## 7. Model Selection Strategy

A key advantage of the Ralph Loop is **role-appropriate model selection**:

| Agent | Priority | Recommended Model | Rationale |
|-------|----------|-------------------|-----------|
| Stage 1 (Triage) | Speed, recall | GPT-4o-mini / Claude Haiku | Fast, cheap, high-recall |
| Agent A (Hypothesizer) | Creativity, breadth | GPT-4o / Claude Sonnet | Needs to spot subtle patterns |
| Agent B (Challenger) | Rigor, precision | GPT-4o / Claude Sonnet | Must reason carefully about safety |
| Agent C (Verifier) | Precision, confidence calibration | GPT-4o / o1 / Claude Opus | Highest quality for final call |

This could reduce cost while improving quality — cheap models for the wide funnel, expensive models only for final verification.

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Cost blowup** | 2-3x more LLM calls per file | Cap total rounds per file; use tiered models; consider fast-exit if Agent A finds nothing |
| **Challenger always concedes** | No adversarial benefit; same as current | Tune challenger prompt to be aggressive; measure concession rate; consider temperature variation |
| **Circular arguments** | A proposes → B refutes → A re-proposes same thing | Track hypothesis history; forbid re-proposing refuted hypotheses; monotonic specificity requirement |
| **Latency** | Sequential rounds are slower | Maintain cross-file parallelism; set tight per-round token limits |
| **Over-engineering** | Current Stage 2 loop may be "good enough" | Measure baseline precision/recall on PyGoat first; only proceed if adversarial loop measurably improves |
| **Prompt complexity** | 3 system prompts to maintain instead of 1 | Clear separation of concerns; each prompt is simpler than the current monolithic Stage 2 prompt |

---

## 9. Migration Path

### Phase 1: Measure Baseline
- Run current pipeline against PyGoat (and other known-vulnerable repos)
- Record: true positives, false positives, false negatives, line-number accuracy
- This is the benchmark to beat

### Phase 2: Implement Challenger Only (A→B loop)
- Easiest incremental change: add Agent B as a falsification check after current Stage 2
- If Agent B refutes, drop the finding; if concedes, keep it
- Measure: does this reduce false positives without killing true positives?

### Phase 3: Full Ralph Loop (A→B→C)
- Replace `_run_stage2()` with `_run_ralph_loop()`
- Add Agent C for precision verification
- Add confidence threshold gating
- Measure improvement over Phase 2

### Phase 4: Model Tiering
- Assign different models per agent role
- Optimize cost/quality tradeoff

---

## 10. Key Implementation Details

### 10.1 Hypothesis History (Prevent Circular Arguments)

```python
@dataclass
class RoundRecord:
    round_number: int
    hypothesis: Hypothesis
    challenge_outcome: Literal["refuted", "conceded", "needs_context"]
    refutation_reason: str | None
    verification_confidence: int | None
    context_gathered: list[ContextSnippet]
```

Each round's record is serialized and included in subsequent prompts. Agents can see what was already tried and refuted.

### 10.2 Monotonic Specificity Enforcement

```python
SPECIFICITY_ORDER = ["file", "function", "line", "line_and_library"]

def validate_specificity_progress(prev: str, current: str) -> bool:
    return SPECIFICITY_ORDER.index(current) >= SPECIFICITY_ORDER.index(prev)
```

If Agent A tries to go backwards in specificity, force it to maintain or advance.

### 10.3 Fast Exit Conditions

- If Agent A's first hypothesis is "no vulnerability found" → skip Agents B and C
- If Agent B refutes with stdlib safety proof → emit `definitive_no_issue` immediately
- If Agent C hits 95%+ confidence on first try → emit finding, no kickback

### 10.4 Context Budget

Current limits should be maintained but partitioned:
- Agent A: up to 3 context requests per round
- Agent B: up to 2 context requests per round (challenger needs less)
- Agent C: up to 2 context requests (focused verification)
- Total context cap across all rounds: 60K chars (same as current `MAX_STAGE2_CONTEXT_CHARS`)

---

## 11. Summary & Recommendation

The current Stage 2 loop is a solid foundation — it already does iterative investigation with context gathering. The Ralph Loop enhancement adds two key capabilities:

1. **Adversarial falsification** (Agent B) — the single highest-value addition. Most false positives in LLM security scanning come from the model convincing itself of a risk that doesn't exist. A dedicated challenger breaks this pattern.

2. **Precision verification** (Agent C) — ensures line-level and library-level accuracy before emitting findings. This directly addresses the goal of "continue until one LLM is certain of the exact line or library causing the issue."

**Recommended approach**: Start with Phase 2 (add Challenger only). This is the smallest change with the biggest impact. If it measurably reduces false positives, proceed to full Ralph Loop.

**Estimated implementation effort**: 
- Phase 2 (Challenger only): ~2-3 days
- Phase 3 (Full Ralph Loop): ~3-5 additional days
- Phase 4 (Model tiering): ~1 day

The existing `call_llm_json`, context resolution, and async infrastructure are fully reusable. The main work is prompt engineering and restructuring the loop control flow in `pipeline.py`.
