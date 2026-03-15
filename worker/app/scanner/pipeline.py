"""Two-stage LLM scanning pipeline with concurrent file processing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.enums import ProcessingStatus, Severity, Stage1Result
from app.models.scan_file import ScanFile
from app.scanner.evidence import (
    EvidenceItem,
    EvidenceRequest,
    add_line_numbers,
    list_python_files,
    read_file,
    resolve_requests,
)
from app.scanner.llm_client import LLMParseError, call_llm_json
from app.scanner.prompts import (
    ARBITER_SYSTEM_PROMPT,
    ARBITER_USER_TEMPLATE,
    CHALLENGER_SYSTEM_PROMPT,
    CHALLENGER_USER_TEMPLATE,
    INVESTIGATOR_SYSTEM_PROMPT,
    INVESTIGATOR_USER_TEMPLATE,
    MAX_STAGE2_CONTEXT_CHARS,
    MAX_STAGE2_CONTEXT_SNIPPETS,
    MAX_STAGE2_INVESTIGATION_ITERATIONS,
    MAX_STAGE2_REQUESTS_PER_ITERATION,
    STAGE1_SYSTEM_PROMPT,
    STAGE1_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


class FindingResult:
    """Intermediate representation of a single finding from stage 2."""

    __slots__ = (
        "file_path",
        "vulnerability_type",
        "severity",
        "line_number",
        "description",
        "explanation",
        "code_snippet",
    )

    def __init__(
        self,
        file_path: str,
        vulnerability_type: str,
        severity: str,
        line_number: int,
        description: str,
        explanation: str,
        code_snippet: str | None = None,
    ):
        self.file_path = file_path
        self.vulnerability_type = vulnerability_type
        self.severity = severity
        self.line_number = line_number
        self.description = description
        self.explanation = explanation
        self.code_snippet = code_snippet


@dataclass(slots=True)
class Stage2Outcome:
    """Normalised result of the stage 2 investigation."""

    verdict: str
    findings: list[FindingResult]
    summary: str = ""
    explanation: str = ""
    blockers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CandidateTerminalSite:
    file_path: str
    line_number: int
    reason: str


@dataclass(slots=True)
class InvestigatorOutput:
    hypothesis: str
    candidate_terminal_sites: list[CandidateTerminalSite]
    specificity: str
    requests: list[EvidenceRequest]
    confidence: float
    known_unknowns: list[str]
    falsification_conditions: list[str]


@dataclass(slots=True)
class ChallengerOutput:
    outcome: str
    counter_hypothesis: str
    rebuttals: list[str]
    requests: list[EvidenceRequest]
    remaining_concerns: list[str]
    narrowing_hint: str
    confidence: float


@dataclass(slots=True)
class ArbiterOutput:
    verdict: str
    confidence: float
    exact_file_path: str
    exact_line_number: int | None
    proof_chain: list[str]
    missing_requirements: list[str]
    summary: str
    finding: FindingResult | None


@dataclass(slots=True)
class RoundRecord:
    round_number: int
    investigator: InvestigatorOutput | None = None
    challenger: ChallengerOutput | None = None
    arbiter: ArbiterOutput | None = None
    evidence_added: int = 0


@dataclass(slots=True)
class InvestigationState:
    suspicious_file_path: str
    current_hypothesis: str = ""
    counter_hypothesis: str = ""
    specificity: str = "file"
    candidate_sink_lines: list[str] = field(default_factory=list)
    candidate_sanitizers: list[str] = field(default_factory=list)
    unresolved_blockers: list[str] = field(default_factory=list)
    external_libraries_touched: list[str] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    round_history: list[RoundRecord] = field(default_factory=list)
    investigator_status: str = "unknown"
    challenger_status: str = "unknown"
    arbiter_status: str = "unknown"
    investigator_confidence: float = 0.0
    challenger_confidence: float = 0.0
    arbiter_confidence: float = 0.0


_VALID_SEVERITIES = {s.value for s in Severity}
_FINAL_VERDICTS = {"definitive_issue", "definitive_no_issue", "uncertain"}
_REQUEST_KINDS = {
    "symbol_definition",
    "symbol_usage",
    "file",
    "import_resolution",
    "class_method_definition",
    "dependency_manifest",
}
_MIN_CHALLENGE_SPECIFICITY = {"function", "line", "line_and_library"}


def _read_file(clone_path: Path, rel_path: str) -> str | None:
    return read_file(clone_path, rel_path)


def _add_line_numbers(content: str) -> str:
    return add_line_numbers(content)


def _list_python_files(clone_path: Path) -> list[str]:
    return list_python_files(clone_path)


def _validate_finding(
    raw: dict[str, Any],
    default_file_path: str,
    known_file_paths: set[str] | None = None,
) -> FindingResult | None:
    """Validate and normalise a single finding dict from stage 2 output."""
    try:
        severity = str(raw.get("severity", "")).lower()
        if severity not in _VALID_SEVERITIES:
            severity = "medium"

        line_number = int(raw.get("line_number", 0))
        if line_number < 1:
            logger.warning("Rejecting finding without exact positive line number in %s", default_file_path)
            return None

        finding_file_path = str(raw.get("file_path", default_file_path)).strip() or default_file_path
        if known_file_paths is not None and finding_file_path not in known_file_paths:
            logger.warning(
                "Stage 2 returned unknown file_path '%s'; falling back to %s",
                finding_file_path,
                default_file_path,
            )
            finding_file_path = default_file_path

        return FindingResult(
            file_path=finding_file_path,
            vulnerability_type=str(raw.get("vulnerability_type", "Unknown")),
            severity=severity,
            line_number=line_number,
            description=str(raw.get("description", "")),
            explanation=str(raw.get("explanation", "")),
            code_snippet=raw.get("code_snippet"),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid finding in %s: %s", default_file_path, exc)
        return None


def _format_repo_index(paths: list[str], max_items: int = 200) -> str:
    if not paths:
        return "- <no Python files found>"

    display = paths[:max_items]
    lines = [f"- {path}" for path in display]
    if len(paths) > max_items:
        lines.append(f"- ... ({len(paths) - max_items} more Python files omitted)")
    return "\n".join(lines)


def _truncate_evidence_for_prompt(evidence_items: list[EvidenceItem]) -> str:
    if not evidence_items:
        return "- No additional repository context gathered yet."

    blocks: list[str] = []
    total_chars = 0
    for item in evidence_items[:MAX_STAGE2_CONTEXT_SNIPPETS]:
        meta = [
            f"label={item.label}",
            f"source_kind={item.source_kind}",
        ]
        if item.file_path:
            meta.append(f"file={item.file_path}")
        if item.line_start is not None and item.line_end is not None:
            meta.append(f"lines={item.line_start}-{item.line_end}")
        if item.rationale:
            meta.append(f"why={item.rationale}")
        block = f"[{'; '.join(meta)}]\n{item.content}"
        if total_chars + len(block) > MAX_STAGE2_CONTEXT_CHARS:
            blocks.append("[Context truncated due to size limits]")
            break
        blocks.append(block)
        total_chars += len(block)
    return "\n\n".join(blocks)


def _format_investigation_state(state: InvestigationState) -> str:
    recent_rounds = []
    for round_record in state.round_history[-3:]:
        summary = [f"round={round_record.round_number}"]
        if round_record.investigator is not None:
            summary.append(f"investigator={round_record.investigator.hypothesis[:120]}")
        if round_record.challenger is not None:
            summary.append(f"challenger={round_record.challenger.outcome}")
        if round_record.arbiter is not None:
            summary.append(f"arbiter={round_record.arbiter.verdict}")
        recent_rounds.append("- " + " | ".join(summary))

    return "\n".join(
        [
            f"suspicious_file_path: {state.suspicious_file_path}",
            f"current_hypothesis: {state.current_hypothesis or '<none>'}",
            f"counter_hypothesis: {state.counter_hypothesis or '<none>'}",
            f"specificity: {state.specificity}",
            f"candidate_sink_lines: {state.candidate_sink_lines or ['<none>']}",
            f"candidate_sanitizers: {state.candidate_sanitizers or ['<none>']}",
            f"unresolved_blockers: {state.unresolved_blockers or ['<none>']}",
            f"external_libraries_touched: {state.external_libraries_touched or ['<none>']}",
            f"role_statuses: investigator={state.investigator_status} challenger={state.challenger_status} arbiter={state.arbiter_status}",
            f"role_confidences: investigator={state.investigator_confidence:.2f} challenger={state.challenger_confidence:.2f} arbiter={state.arbiter_confidence:.2f}",
            "recent_round_history:",
            *(recent_rounds or ["- <none>"]),
        ]
    )


def _append_round_record(state: InvestigationState, round_record: RoundRecord) -> None:
    state.round_history.append(round_record)


def _merge_evidence(state: InvestigationState, new_items: list[EvidenceItem]) -> int:
    seen = {(item.label, item.file_path, item.line_start, item.line_end) for item in state.evidence_items}
    added = 0
    for item in new_items:
        key = (item.label, item.file_path, item.line_start, item.line_end)
        if key in seen:
            continue
        seen.add(key)
        state.evidence_items.append(item)
        added += 1
    state.evidence_items = state.evidence_items[:MAX_STAGE2_CONTEXT_SNIPPETS]
    return added


def _normalise_requests(raw_requests: Any) -> list[EvidenceRequest]:
    if not isinstance(raw_requests, list):
        return []

    normalised: list[EvidenceRequest] = []
    for raw in raw_requests[:MAX_STAGE2_REQUESTS_PER_ITERATION]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind", "")).strip().lower()
        if kind not in _REQUEST_KINDS:
            continue
        normalised.append(
            EvidenceRequest(
                kind=kind,
                symbol=str(raw.get("symbol", "")).strip(),
                file_path=str(raw.get("file_path", "")).strip(),
                class_name=str(raw.get("class_name", "")).strip(),
                method_name=str(raw.get("method_name", "")).strip(),
                dependency_name=str(raw.get("dependency_name", "")).strip(),
                why=str(raw.get("why", "")).strip(),
            )
        )
    return normalised


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_candidate_terminal_sites(raw_sites: Any) -> list[CandidateTerminalSite]:
    if not isinstance(raw_sites, list):
        return []
    sites: list[CandidateTerminalSite] = []
    for raw in raw_sites:
        if not isinstance(raw, dict):
            continue
        try:
            line_number = int(raw.get("line_number", 0))
        except (TypeError, ValueError):
            line_number = 0
        sites.append(
            CandidateTerminalSite(
                file_path=str(raw.get("file_path", "")).strip(),
                line_number=line_number,
                reason=str(raw.get("reason", "")).strip(),
            )
        )
    return sites


def _parse_investigator_output(raw: dict[str, Any]) -> InvestigatorOutput:
    return InvestigatorOutput(
        hypothesis=str(raw.get("hypothesis", "")).strip(),
        candidate_terminal_sites=_parse_candidate_terminal_sites(raw.get("candidate_terminal_sites", [])),
        specificity=str(raw.get("specificity", "file")).strip().lower() or "file",
        requests=_normalise_requests(raw.get("requests", [])),
        confidence=_safe_float(raw.get("confidence", 0.0)),
        known_unknowns=[str(x).strip() for x in raw.get("known_unknowns", []) if str(x).strip()] if isinstance(raw.get("known_unknowns", []), list) else [],
        falsification_conditions=[str(x).strip() for x in raw.get("falsification_conditions", []) if str(x).strip()] if isinstance(raw.get("falsification_conditions", []), list) else [],
    )


def _parse_challenger_output(raw: dict[str, Any]) -> ChallengerOutput:
    outcome = str(raw.get("outcome", "needs_context")).strip().lower()
    if outcome not in {"refuted", "conceded", "needs_context"}:
        outcome = "needs_context"
    return ChallengerOutput(
        outcome=outcome,
        counter_hypothesis=str(raw.get("counter_hypothesis", "")).strip(),
        rebuttals=[str(x).strip() for x in raw.get("rebuttals", []) if str(x).strip()] if isinstance(raw.get("rebuttals", []), list) else [],
        requests=_normalise_requests(raw.get("requests", [])),
        remaining_concerns=[str(x).strip() for x in raw.get("remaining_concerns", []) if str(x).strip()] if isinstance(raw.get("remaining_concerns", []), list) else [],
        narrowing_hint=str(raw.get("narrowing_hint", "")).strip(),
        confidence=_safe_float(raw.get("confidence", 0.0)),
    )


def _parse_arbiter_output(
    raw: dict[str, Any],
    file_path: str,
    known_file_paths: set[str],
) -> ArbiterOutput:
    verdict = str(raw.get("verdict", "continue")).strip().lower()
    if verdict not in {"definitive_issue", "definitive_no_issue", "continue"}:
        verdict = "continue"

    exact_file_path = str(raw.get("exact_file_path", "")).strip()
    exact_line_number_raw = raw.get("exact_line_number")
    try:
        exact_line_number = int(exact_line_number_raw) if exact_line_number_raw is not None else None
    except (TypeError, ValueError):
        exact_line_number = None

    finding = None
    if verdict == "definitive_issue" and isinstance(raw.get("finding"), dict):
        finding = _validate_finding(raw["finding"], file_path, known_file_paths)
        if finding is None:
            verdict = "continue"
        elif not exact_file_path or exact_line_number != finding.line_number:
            logger.warning("Rejecting speculative arbiter issue for %s due to missing exact attribution", file_path)
            verdict = "continue"
            finding = None
        elif exact_file_path not in known_file_paths:
            logger.warning("Rejecting arbiter issue for unknown path %s", exact_file_path)
            verdict = "continue"
            finding = None

    if verdict == "definitive_no_issue":
        finding = None

    return ArbiterOutput(
        verdict=verdict,
        confidence=_safe_float(raw.get("confidence", 0.0)),
        exact_file_path=exact_file_path,
        exact_line_number=exact_line_number,
        proof_chain=[str(x).strip() for x in raw.get("proof_chain", []) if str(x).strip()] if isinstance(raw.get("proof_chain", []), list) else [],
        missing_requirements=[str(x).strip() for x in raw.get("missing_requirements", []) if str(x).strip()] if isinstance(raw.get("missing_requirements", []), list) else [],
        summary=str(raw.get("summary", "")).strip(),
        finding=finding,
    )


def _resolve_requests(clone_path: Path, requests: list[EvidenceRequest]) -> list[EvidenceItem]:
    return resolve_requests(clone_path, requests)


def _run_stage1(file_path: str, content: str) -> Stage1Result:
    numbered = _add_line_numbers(content)
    user_prompt = STAGE1_USER_TEMPLATE.format(file_path=file_path, file_content=numbered)
    try:
        result = call_llm_json(STAGE1_SYSTEM_PROMPT, user_prompt, role_name="stage1")
        classification = result.get("classification", "").lower()
        if classification == "suspicious":
            return Stage1Result.suspicious
        if classification == "not_suspicious":
            return Stage1Result.not_suspicious
        logger.warning(
            "Unexpected stage1 classification '%s' for %s, treating as suspicious",
            classification,
            file_path,
        )
        return Stage1Result.suspicious
    except LLMParseError:
        logger.warning("Stage 1 parse failure for %s, treating as failed", file_path)
        return Stage1Result.failed


def _run_investigator_step(
    file_path: str,
    numbered_content: str,
    repo_index: str,
    state: InvestigationState,
    round_number: int,
) -> InvestigatorOutput:
    user_prompt = INVESTIGATOR_USER_TEMPLATE.format(
        round_number=round_number,
        max_rounds=MAX_STAGE2_INVESTIGATION_ITERATIONS,
        file_path=file_path,
        file_content=numbered_content,
        repo_index=repo_index,
        investigation_state=_format_investigation_state(state),
        supplemental_context=_truncate_evidence_for_prompt(state.evidence_items),
    )
    raw = call_llm_json(
        INVESTIGATOR_SYSTEM_PROMPT,
        user_prompt,
        role_name="investigator",
        temperature=0.2,
    )
    return _parse_investigator_output(raw)


def _run_challenger_step(
    file_path: str,
    state: InvestigationState,
    round_number: int,
) -> ChallengerOutput:
    user_prompt = CHALLENGER_USER_TEMPLATE.format(
        round_number=round_number,
        max_rounds=MAX_STAGE2_INVESTIGATION_ITERATIONS,
        file_path=file_path,
        investigation_state=_format_investigation_state(state),
        supplemental_context=_truncate_evidence_for_prompt(state.evidence_items),
    )
    raw = call_llm_json(
        CHALLENGER_SYSTEM_PROMPT,
        user_prompt,
        role_name="challenger",
        temperature=0.1,
    )
    return _parse_challenger_output(raw)


def _run_arbiter_step(
    file_path: str,
    state: InvestigationState,
    round_number: int,
    known_file_paths: set[str],
    must_finalize: bool,
) -> ArbiterOutput:
    user_prompt = ARBITER_USER_TEMPLATE.format(
        round_number=round_number,
        max_rounds=MAX_STAGE2_INVESTIGATION_ITERATIONS,
        file_path=file_path,
        must_finalize="yes" if must_finalize else "no",
        investigation_state=_format_investigation_state(state),
        supplemental_context=_truncate_evidence_for_prompt(state.evidence_items),
    )
    raw = call_llm_json(
        ARBITER_SYSTEM_PROMPT,
        user_prompt,
        role_name="arbiter",
        temperature=0.0,
    )
    return _parse_arbiter_output(raw, file_path, known_file_paths)


def _advance_investigation_state(
    state: InvestigationState,
    investigator: InvestigatorOutput,
    challenger: ChallengerOutput | None,
    arbiter: ArbiterOutput,
) -> None:
    state.current_hypothesis = investigator.hypothesis
    state.specificity = investigator.specificity
    state.investigator_status = "active" if investigator.hypothesis else "uncertain"
    state.investigator_confidence = investigator.confidence
    state.unresolved_blockers = investigator.known_unknowns[:]
    state.candidate_sink_lines = [
        f"{site.file_path}:{site.line_number} - {site.reason}" for site in investigator.candidate_terminal_sites if site.file_path and site.line_number > 0
    ]

    if challenger is not None:
        state.counter_hypothesis = challenger.counter_hypothesis
        state.challenger_status = challenger.outcome
        state.challenger_confidence = challenger.confidence
        for concern in challenger.remaining_concerns:
            if concern not in state.unresolved_blockers:
                state.unresolved_blockers.append(concern)

    state.arbiter_status = arbiter.verdict
    state.arbiter_confidence = arbiter.confidence
    for item in state.evidence_items:
        if item.request_kind == "dependency_manifest" and item.symbol and item.symbol not in state.external_libraries_touched:
            state.external_libraries_touched.append(item.symbol)


def _run_stage2(clone_path: Path, file_path: str, content: str) -> Stage2Outcome:
    """Run sequential multi-role security analysis on a suspicious file."""
    numbered = _add_line_numbers(content)
    repo_paths = _list_python_files(clone_path)
    known_file_paths = set(repo_paths)
    known_file_paths.add(file_path)
    repo_index = _format_repo_index(repo_paths)
    state = InvestigationState(suspicious_file_path=file_path)

    for round_number in range(1, MAX_STAGE2_INVESTIGATION_ITERATIONS + 1):
        must_finalize = round_number == MAX_STAGE2_INVESTIGATION_ITERATIONS
        round_record = RoundRecord(round_number=round_number)

        investigator = _run_investigator_step(file_path, numbered, repo_index, state, round_number)
        round_record.investigator = investigator
        investigator_evidence = _resolve_requests(clone_path, investigator.requests)
        round_record.evidence_added += _merge_evidence(state, investigator_evidence)

        challenger: ChallengerOutput | None = None
        if investigator.hypothesis and investigator.specificity in _MIN_CHALLENGE_SPECIFICITY:
            challenger = _run_challenger_step(file_path, state, round_number)
            round_record.challenger = challenger
            challenger_evidence = _resolve_requests(clone_path, challenger.requests)
            round_record.evidence_added += _merge_evidence(state, challenger_evidence)

        arbiter = _run_arbiter_step(file_path, state, round_number, known_file_paths, must_finalize)
        round_record.arbiter = arbiter
        _advance_investigation_state(state, investigator, challenger, arbiter)
        _append_round_record(state, round_record)

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

        if arbiter.verdict == "definitive_issue" and arbiter.finding is not None:
            return Stage2Outcome(
                verdict="definitive_issue",
                findings=[arbiter.finding],
                summary=arbiter.summary,
                explanation="; ".join(arbiter.proof_chain),
            )
        if arbiter.verdict == "definitive_no_issue":
            return Stage2Outcome(
                verdict="definitive_no_issue",
                findings=[],
                summary=arbiter.summary,
                explanation="; ".join(arbiter.proof_chain),
            )

    summary = "Investigation remained uncertain after the maximum number of rounds."
    blockers = state.unresolved_blockers or ["Insufficient evidence to prove or disprove the hypothesis."]
    logger.info(
        "Stage 2 uncertain file=%s rounds=%d evidence=%d blockers=%s",
        file_path,
        len(state.round_history),
        len(state.evidence_items),
        blockers,
    )
    return Stage2Outcome(
        verdict="uncertain",
        findings=[],
        summary=summary,
        explanation=state.current_hypothesis or state.counter_hypothesis,
        blockers=blockers,
    )


def _process_file(
    clone_path: Path,
    scan_file: ScanFile,
) -> tuple[ScanFile, list[FindingResult]]:
    findings: list[FindingResult] = []

    content = _read_file(clone_path, scan_file.file_path)
    if content is None:
        scan_file.stage1_result = Stage1Result.failed
        scan_file.processing_status = ProcessingStatus.failed
        scan_file.error_message = "Could not read file"
        return scan_file, findings

    stripped = content.strip()
    if not stripped or len(stripped) < 10:
        scan_file.stage1_result = Stage1Result.not_suspicious
        scan_file.processing_status = ProcessingStatus.skipped
        return scan_file, findings

    stage1 = _run_stage1(scan_file.file_path, content)
    scan_file.stage1_result = stage1

    if stage1 == Stage1Result.not_suspicious:
        scan_file.processing_status = ProcessingStatus.complete
        return scan_file, findings

    if stage1 == Stage1Result.failed:
        scan_file.processing_status = ProcessingStatus.failed
        scan_file.error_message = "Stage 1 classification failed after retries"
        return scan_file, findings

    scan_file.stage2_attempted = True
    try:
        outcome = _run_stage2(clone_path, scan_file.file_path, content)
        findings = outcome.findings
        scan_file.processing_status = ProcessingStatus.complete
        if outcome.verdict == "uncertain":
            details = "; ".join(outcome.blockers) if outcome.blockers else outcome.summary
            scan_file.error_message = f"Stage 2 uncertain: {details}"
        else:
            scan_file.error_message = None
    except LLMParseError as exc:
        scan_file.processing_status = ProcessingStatus.failed
        scan_file.error_message = f"Stage 2 failed: {exc}"

    return scan_file, findings


async def _process_file_async(
    clone_path: Path,
    scan_file: ScanFile,
    semaphore: asyncio.Semaphore,
) -> tuple[ScanFile, list[FindingResult]]:
    async with semaphore:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _process_file, clone_path, scan_file)


async def _run_pipeline_async(
    clone_path: Path,
    scan_files: list[ScanFile],
) -> list[FindingResult]:
    semaphore = asyncio.Semaphore(settings.max_concurrent_files)
    tasks = [_process_file_async(clone_path, sf, semaphore) for sf in scan_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_findings: list[FindingResult] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("Unexpected error processing %s: %s", scan_files[i].file_path, result)
            scan_files[i].processing_status = ProcessingStatus.failed
            scan_files[i].error_message = f"Unexpected error: {result}"
        else:
            _, findings = result
            all_findings.extend(findings)

    return all_findings


def run_scan_pipeline(
    db: Session,
    scan_id: uuid.UUID,
    clone_path: Path,
    scan_files: list[ScanFile],
) -> list[FindingResult]:
    if not scan_files:
        logger.info("Scan %s: no files to process", scan_id)
        return []

    logger.info("Scan %s: starting LLM pipeline on %d files", scan_id, len(scan_files))
    all_findings = asyncio.run(_run_pipeline_async(clone_path, scan_files))
    db.flush()

    suspicious_count = sum(1 for sf in scan_files if sf.stage1_result == Stage1Result.suspicious)
    failed_count = sum(1 for sf in scan_files if sf.processing_status == ProcessingStatus.failed)
    uncertain_count = sum(1 for sf in scan_files if (sf.error_message or "").startswith("Stage 2 uncertain:"))
    logger.info(
        "Scan %s pipeline complete: %d files, %d suspicious, %d findings, %d uncertain, %d file failures",
        scan_id,
        len(scan_files),
        suspicious_count,
        len(all_findings),
        uncertain_count,
        failed_count,
    )

    return all_findings
