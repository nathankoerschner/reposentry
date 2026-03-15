"""Tests for the Ralph-loop LLM scanning pipeline."""

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.enums import ProcessingStatus, Stage1Result
from app.scanner.evidence import (
    EvidenceItem,
    load_dependency_manifests,
    resolve_class_method_definition,
)
from app.scanner.llm_client import LLMParseError, _extract_json
from app.scanner.pipeline import (
    FindingResult,
    InvestigationState,
    Stage2Outcome,
    _list_python_files,
    _merge_evidence,
    _normalise_requests,
    _parse_arbiter_output,
    _process_file,
    _run_stage1,
    _run_stage2,
    _truncate_evidence_for_prompt,
    _validate_finding,
)


class TestExtractJson:
    def test_plain_json(self):
        raw = '{"classification": "suspicious", "reason": "uses eval"}'
        result = _extract_json(raw)
        assert result["classification"] == "suspicious"

    def test_json_with_markdown_fences(self):
        raw = '```json\n{"classification": "not_suspicious", "reason": "constants"}\n```'
        result = _extract_json(raw)
        assert result["classification"] == "not_suspicious"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json at all")


class TestValidateFinding:
    def test_valid_finding(self):
        raw = {
            "file_path": "app.py",
            "vulnerability_type": "SQL Injection",
            "severity": "high",
            "line_number": 42,
            "description": "User input in query",
            "explanation": "Attacker can inject SQL",
            "code_snippet": "cursor.execute(f'SELECT * FROM {user_input}')",
        }
        finding = _validate_finding(raw, "app.py", {"app.py"})
        assert finding is not None
        assert finding.file_path == "app.py"
        assert finding.line_number == 42

    def test_invalid_line_number_is_rejected(self):
        raw = {
            "vulnerability_type": "XSS",
            "severity": "low",
            "line_number": 0,
            "description": "desc",
            "explanation": "exp",
        }
        assert _validate_finding(raw, "app.py", {"app.py"}) is None


class TestRunStage1:
    @patch("app.scanner.pipeline.call_llm_json")
    def test_suspicious(self, mock_call):
        mock_call.return_value = {"classification": "suspicious", "reason": "uses eval"}
        result = _run_stage1("app.py", "eval(input())")
        assert result == Stage1Result.suspicious
        assert mock_call.call_args.kwargs["role_name"] == "stage1"

    @patch("app.scanner.pipeline.call_llm_json")
    def test_parse_failure(self, mock_call):
        mock_call.side_effect = LLMParseError("bad json")
        result = _run_stage1("app.py", "code")
        assert result == Stage1Result.failed


class TestEvidenceHelpers:
    def test_respects_default_exclusions(self, tmp_path: Path):
        (tmp_path / "app").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / ".venv").mkdir()
        (tmp_path / "app" / "main.py").write_text("print('ok')")
        (tmp_path / "tests" / "test_main.py").write_text("assert True")
        (tmp_path / ".venv" / "ignored.py").write_text("print('ignore')")

        assert _list_python_files(tmp_path) == ["app/main.py"]

    def test_merge_evidence_deduplicates(self):
        state = InvestigationState(suspicious_file_path="app.py")
        item = EvidenceItem("file", "A", "app.py", "body", 1, 2)
        added = _merge_evidence(state, [item, item])
        assert added == 1
        assert len(state.evidence_items) == 1

    def test_truncate_evidence_formats_items(self):
        text = _truncate_evidence_for_prompt(
            [EvidenceItem("file", "A", "app.py", "File: app.py", 1, 2, rationale="needed")]
        )
        assert "label=A" in text
        assert "why=needed" in text

    def test_normalise_requests_supports_new_request_kinds(self):
        requests = _normalise_requests(
            [
                {
                    "kind": "class_method_definition",
                    "class_name": "Danger",
                    "method_name": "run",
                    "why": "inspect wrapper",
                },
                {"kind": "dependency_manifest", "dependency_name": "django", "why": "ground dependency"},
            ]
        )
        assert [request.kind for request in requests] == ["class_method_definition", "dependency_manifest"]

    def test_class_method_resolution(self, tmp_path: Path):
        file_path = tmp_path / "app.py"
        file_path.write_text("class Danger:\n    def run(self):\n        return 1\n")
        evidence = resolve_class_method_definition(tmp_path, "Danger", "run")
        assert evidence
        assert evidence[0].file_path == "app.py"

    def test_dependency_manifest_loading(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("django==5.0\nrequests==2.0\n")
        evidence = load_dependency_manifests(tmp_path, "django")
        assert evidence
        assert evidence[0].file_path == "requirements.txt"


class TestArbiterParsing:
    def test_rejects_issue_without_exact_line_match(self):
        raw = {
            "verdict": "definitive_issue",
            "confidence": 0.9,
            "exact_file_path": "app.py",
            "exact_line_number": 10,
            "proof_chain": ["input reaches sink"],
            "missing_requirements": [],
            "summary": "confirmed",
            "finding": {
                "file_path": "app.py",
                "vulnerability_type": "SQL Injection",
                "severity": "high",
                "line_number": 11,
                "description": "desc",
                "explanation": "exp",
            },
        }
        parsed = _parse_arbiter_output(raw, "app.py", {"app.py"})
        assert parsed.verdict == "continue"
        assert parsed.finding is None


class TestRunStage2:
    @patch("app.scanner.pipeline._list_python_files")
    @patch("app.scanner.pipeline.call_llm_json")
    def test_returns_definitive_issue(self, mock_call, mock_list):
        mock_list.return_value = ["app.py", "db.py"]
        mock_call.side_effect = [
            {
                "hypothesis": "user input reaches database execute",
                "candidate_terminal_sites": [{"file_path": "db.py", "line_number": 10, "reason": "execute sink"}],
                "specificity": "line",
                "requests": [],
                "confidence": 0.7,
                "known_unknowns": [],
                "falsification_conditions": [],
            },
            {
                "outcome": "conceded",
                "counter_hypothesis": "",
                "rebuttals": [],
                "requests": [],
                "remaining_concerns": [],
                "narrowing_hint": "",
                "confidence": 0.6,
            },
            {
                "verdict": "definitive_issue",
                "confidence": 0.9,
                "exact_file_path": "db.py",
                "exact_line_number": 10,
                "proof_chain": ["request param flows into execute"],
                "missing_requirements": [],
                "summary": "Confirmed SQL injection sink.",
                "finding": {
                    "file_path": "db.py",
                    "vulnerability_type": "SQL Injection",
                    "severity": "high",
                    "line_number": 10,
                    "description": "desc",
                    "explanation": "exp",
                    "code_snippet": "code",
                },
            },
        ]
        outcome = _run_stage2(Path("/tmp/clone"), "app.py", "code")
        assert outcome.verdict == "definitive_issue"
        assert len(outcome.findings) == 1
        assert outcome.findings[0].file_path == "db.py"
        assert mock_call.call_args_list[0].kwargs["role_name"] == "investigator"
        assert mock_call.call_args_list[1].kwargs["role_name"] == "challenger"
        assert mock_call.call_args_list[2].kwargs["role_name"] == "arbiter"

    @patch("app.scanner.pipeline._resolve_requests")
    @patch("app.scanner.pipeline._list_python_files")
    @patch("app.scanner.pipeline.call_llm_json")
    def test_iterates_then_returns_uncertain(self, mock_call, mock_list, mock_resolve):
        mock_list.return_value = ["app.py", "helpers.py"]
        mock_resolve.return_value = [EvidenceItem("file", "helper", "helpers.py", "body", 1, 4)]
        investigator = {
            "hypothesis": "maybe sanitized in helper",
            "candidate_terminal_sites": [],
            "specificity": "function",
            "requests": [{"kind": "symbol_definition", "symbol": "sanitize", "why": "inspect helper"}],
            "confidence": 0.4,
            "known_unknowns": ["Need helper definition"],
            "falsification_conditions": [],
        }
        challenger = {
            "outcome": "needs_context",
            "counter_hypothesis": "helper may sanitize input",
            "rebuttals": ["No helper body seen"],
            "requests": [],
            "remaining_concerns": ["helper unresolved"],
            "narrowing_hint": "inspect sanitize",
            "confidence": 0.3,
        }
        arbiter = {
            "verdict": "continue",
            "confidence": 0.2,
            "exact_file_path": "",
            "exact_line_number": 0,
            "proof_chain": [],
            "missing_requirements": ["Need helper definition"],
            "summary": "Need more evidence",
            "finding": None,
        }
        mock_call.side_effect = [investigator, challenger, arbiter] * 10

        outcome = _run_stage2(Path("/tmp/clone"), "app.py", "code")

        assert outcome.verdict == "uncertain"
        assert "Need helper definition" in outcome.blockers or "helper unresolved" in outcome.blockers
        assert mock_resolve.called

    @patch("app.scanner.pipeline._list_python_files")
    @patch("app.scanner.pipeline.call_llm_json")
    def test_parse_failure_raises(self, mock_call, mock_list):
        mock_list.return_value = ["app.py"]
        mock_call.side_effect = LLMParseError("bad")
        with pytest.raises(LLMParseError):
            _run_stage2(Path("/tmp/clone"), "app.py", "code")


class TestProcessFile:
    def test_suspicious_file_gets_stage2(self):
        with (
            patch("app.scanner.pipeline._read_file", return_value="import os\nos.system(input())"),
            patch("app.scanner.pipeline._run_stage1", return_value=Stage1Result.suspicious),
            patch(
                "app.scanner.pipeline._run_stage2",
                return_value=Stage2Outcome(
                    verdict="definitive_issue",
                    findings=[FindingResult("app.py", "Command Injection", "critical", 2, "d", "e", "c")],
                    summary="Confirmed dangerous sink.",
                ),
            ),
        ):
            result = _process_file(Path("/tmp/clone"), uuid.uuid4(), "app.py")

        assert result.stage2_attempted is True
        assert result.processing_status == ProcessingStatus.complete
        assert len(result.findings) == 1

    def test_uncertain_records_metadata(self):
        with (
            patch("app.scanner.pipeline._read_file", return_value="import os\nos.system(input())"),
            patch("app.scanner.pipeline._run_stage1", return_value=Stage1Result.suspicious),
            patch(
                "app.scanner.pipeline._run_stage2",
                return_value=Stage2Outcome(
                    verdict="uncertain",
                    findings=[],
                    summary="Could not prove wrapper behavior.",
                    blockers=["Need helper definition"],
                ),
            ),
        ):
            result = _process_file(Path("/tmp/clone"), uuid.uuid4(), "app.py")

        assert result.processing_status == ProcessingStatus.complete
        assert "Stage 2 uncertain" in result.error_message
        assert "Need helper definition" in result.error_message
        assert result.findings == []
