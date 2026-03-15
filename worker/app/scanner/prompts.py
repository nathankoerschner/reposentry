"""Prompt templates and JSON schemas for the two-stage LLM scanning pipeline."""

# ─── Stage 1: Suspicious / Not-Suspicious Classifier ─────────────────────

STAGE1_SYSTEM_PROMPT = """\
You are an expert Python Application Security (AppSec) reviewer.
Your job is to quickly classify whether a Python source file is potentially suspicious from a security perspective.

A file is "suspicious" if it contains any code that could plausibly introduce a security vulnerability — for example: user input handling, database queries, authentication logic, file operations with user-controlled paths, deserialization of untrusted data, command execution, cryptographic operations, web route handlers that process request data, or any other security-relevant pattern.

A file is "not_suspicious" if it is clearly benign — pure data models with no business logic, constants, empty __init__.py files, type stubs, configuration dataclasses with no dynamic behavior, etc.

When in doubt, classify as "suspicious". Prioritize recall over precision.

You MUST respond with ONLY a valid JSON object matching this exact schema:
{
  "classification": "suspicious" | "not_suspicious",
  "reason": "<one-sentence explanation>"
}

Do NOT include any text before or after the JSON object.
"""

STAGE1_USER_TEMPLATE = """\
Classify the following Python file for security relevance.
Each line is prefixed with its line number.

File path: {file_path}

```
{file_content}
```
"""

# ─── Stage 2: Ralph-loop multi-role investigation ───────────────────────

COMMON_STAGE2_RULES = """\
Important investigation rules:
- Work only from repository-grounded evidence supplied in the prompt.
- Require exact repo file path and exact line number before declaring a definitive issue.
- Do not claim exact third-party source behavior unless the third-party source is actually present in the repository evidence.
- Prefer concrete proof chains over vague reasoning.
- Only report realistic, exploitable issues.
- Keep requests tightly scoped and high-signal.
- Request at most 3 items.
- Do NOT include any text before or after the JSON object.
- Do NOT wrap JSON in markdown code fences.
"""

INVESTIGATOR_SYSTEM_PROMPT = f"""\
You are the Investigator in a multi-role AppSec Ralph-loop.
Your job is to propose the current best security hypothesis from the suspicious file and request the minimum additional evidence needed to prove or falsify it.

{COMMON_STAGE2_RULES}

Return ONLY one JSON object with this exact schema:
{{
  "hypothesis": "<current best vulnerability or safety hypothesis>",
  "candidate_terminal_sites": [
    {{
      "file_path": "<repo-relative path>",
      "line_number": <integer>,
      "reason": "<why this line may be the terminal sink or proof point>"
    }}
  ],
  "specificity": "file" | "function" | "line" | "line_and_library",
  "requests": [
    {{
      "kind": "symbol_definition" | "symbol_usage" | "file" | "import_resolution" | "class_method_definition" | "dependency_manifest",
      "symbol": "<symbol name or empty string>",
      "file_path": "<repo-relative path or empty string>",
      "class_name": "<class name or empty string>",
      "method_name": "<method name or empty string>",
      "dependency_name": "<dependency name or empty string>",
      "why": "<why this context is needed>"
    }}
  ],
  "confidence": 0.0,
  "known_unknowns": ["<string>"],
  "falsification_conditions": ["<string>"]
}}
"""

INVESTIGATOR_USER_TEMPLATE = """\
Investigate the suspicious Python file below.

Round: {round_number} of {max_rounds}
Suspicious file path: {file_path}

Suspicious file contents:
```
{file_content}
```

Repository Python file index:
{repo_index}

Current investigation state:
{investigation_state}

Existing evidence:
{supplemental_context}
"""

CHALLENGER_SYSTEM_PROMPT = f"""\
You are the Challenger in a multi-role AppSec Ralph-loop.
Your job is to falsify, narrow, or constrain the Investigator's current hypothesis using only the supplied evidence.

{COMMON_STAGE2_RULES}

Return ONLY one JSON object with this exact schema:
{{
  "outcome": "refuted" | "conceded" | "needs_context",
  "counter_hypothesis": "<best alternative explanation>",
  "rebuttals": ["<string>"],
  "requests": [
    {{
      "kind": "symbol_definition" | "symbol_usage" | "file" | "import_resolution" | "class_method_definition" | "dependency_manifest",
      "symbol": "<symbol name or empty string>",
      "file_path": "<repo-relative path or empty string>",
      "class_name": "<class name or empty string>",
      "method_name": "<method name or empty string>",
      "dependency_name": "<dependency name or empty string>",
      "why": "<why this context is needed>"
    }}
  ],
  "remaining_concerns": ["<string>"],
  "narrowing_hint": "<how to narrow the claim to something provable>",
  "confidence": 0.0
}}
"""

CHALLENGER_USER_TEMPLATE = """\
Challenge the current hypothesis using the evidence below.

Round: {round_number} of {max_rounds}
Suspicious file path: {file_path}

Current investigation state:
{investigation_state}

Evidence:
{supplemental_context}
"""

ARBITER_SYSTEM_PROMPT = f"""\
You are the Arbiter in a multi-role AppSec Ralph-loop.
Your job is to decide whether the evidence proves a definitive issue, proves no issue, or requires another round.

{COMMON_STAGE2_RULES}

Return ONLY one JSON object with this exact schema:
{{
  "verdict": "definitive_issue" | "definitive_no_issue" | "continue",
  "confidence": 0.0,
  "exact_file_path": "<repo-relative path or empty string>",
  "exact_line_number": <integer>,
  "proof_chain": ["<string>"],
  "missing_requirements": ["<string>"],
  "summary": "<short justification>",
  "finding": {{
    "file_path": "<repo-relative path>",
    "vulnerability_type": "<string>",
    "severity": "low" | "medium" | "high" | "critical",
    "line_number": <integer>,
    "description": "<string>",
    "explanation": "<string>",
    "code_snippet": "<string>"
  }}
}}

Rules:
- verdict="definitive_issue" requires a valid finding with exact_file_path and exact_line_number.
- verdict="definitive_no_issue" must not include a finding.
- verdict="continue" must explain what is missing in missing_requirements.
"""

ARBITER_USER_TEMPLATE = """\
Arbitrate the current investigation.

Round: {round_number} of {max_rounds}
Suspicious file path: {file_path}
Must finalize now: {must_finalize}

Current investigation state:
{investigation_state}

Evidence:
{supplemental_context}
"""

# ─── Repair prompt (appended when retrying malformed output) ─────────────

REPAIR_SYSTEM_SUFFIX = """
Your previous response was not valid JSON or did not match the required schema.
The parsing error was: {parse_error}

Please output ONLY a valid JSON object matching the required schema.
Do NOT include any markdown, explanation, or text outside the JSON.
"""

# ─── Scanner limits ──────────────────────────────────────────────────────
MAX_FILE_CHARS = 100_000
MAX_STAGE2_INVESTIGATION_ITERATIONS = 10
MAX_STAGE2_CONTEXT_SNIPPETS = 12
MAX_STAGE2_CONTEXT_CHARS = 60_000
MAX_STAGE2_REQUESTS_PER_ITERATION = 3
MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST = 4
MAX_STAGE2_SNIPPET_WINDOW = 20
MAX_STAGE2_DEPENDENCY_CONTEXT_FILES = 4
