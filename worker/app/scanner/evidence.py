"""Deterministic repository evidence gathering for Stage 2 investigations."""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.scanner.file_discovery import discover_python_files
from app.scanner.prompts import (
    MAX_FILE_CHARS,
    MAX_STAGE2_DEPENDENCY_CONTEXT_FILES,
    MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST,
    MAX_STAGE2_SNIPPET_WINDOW,
)

logger = logging.getLogger(__name__)

_DEPENDENCY_MANIFEST_CANDIDATES = [
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
]


@dataclass(slots=True)
class ContextSnippet:
    """Additional repo context gathered during investigation."""

    label: str
    content: str


@dataclass(slots=True)
class EvidenceItem:
    """Structured evidence attached to investigation state."""

    source_kind: str
    label: str
    file_path: str | None
    content: str
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None
    request_kind: str | None = None
    rationale: str = ""


@dataclass(slots=True)
class EvidenceRequest:
    """Normalized LLM request for additional context."""

    kind: str
    symbol: str = ""
    file_path: str = ""
    class_name: str = ""
    method_name: str = ""
    dependency_name: str = ""
    why: str = ""


def read_file(clone_path: Path, rel_path: str) -> str | None:
    """Read a file's contents, returning None if unreadable."""
    full = clone_path / rel_path
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + "\n# ... [truncated for analysis]\n"
        return content
    except Exception as exc:
        logger.warning("Cannot read %s: %s", rel_path, exc)
        return None


def add_line_numbers(content: str) -> str:
    """Prepend line numbers to each line of file content."""
    lines = content.split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{i:{width}d} | {line}" for i, line in enumerate(lines, 1))


def list_python_files(clone_path: Path) -> list[str]:
    """Return repository-relative Python files using the main discovery rules."""
    return discover_python_files(clone_path)


def _make_snippet(file_path: str, lines: list[str], start: int, end: int, label: str) -> ContextSnippet:
    numbered = "\n".join(f"{line_no:4d} | {lines[line_no - 1]}" for line_no in range(start, end + 1))
    return ContextSnippet(
        label=label,
        content=f"File: {file_path}\nLines: {start}-{end}\n```\n{numbered}\n```",
    )


def _snippet_to_evidence(
    snippet: ContextSnippet,
    *,
    source_kind: str,
    file_path: str | None,
    symbol: str | None,
    request_kind: str,
    rationale: str,
) -> EvidenceItem:
    line_start = None
    line_end = None
    match = re.search(r"Lines:\s*(\d+)-(\d+)", snippet.content)
    if match:
        line_start = int(match.group(1))
        line_end = int(match.group(2))
    return EvidenceItem(
        source_kind=source_kind,
        label=snippet.label,
        file_path=file_path,
        content=snippet.content,
        line_start=line_start,
        line_end=line_end,
        symbol=symbol,
        request_kind=request_kind,
        rationale=rationale,
    )


def search_symbol_definitions(clone_path: Path, symbol: str) -> list[EvidenceItem]:
    if not symbol:
        return []

    pattern = re.compile(
        rf"^\s*(?:async\s+def|def|class)\s+{re.escape(symbol)}\b|^\s*{re.escape(symbol)}\s*=",
        re.MULTILINE,
    )
    evidence: list[EvidenceItem] = []
    for file_path in list_python_files(clone_path):
        content = read_file(clone_path, file_path)
        if not content:
            continue
        lines = content.splitlines()
        for idx, line in enumerate(lines, 1):
            if pattern.search(line):
                start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
                end = min(len(lines), idx + MAX_STAGE2_SNIPPET_WINDOW)
                snippet = _make_snippet(file_path, lines, start, end, f"Definition of symbol '{symbol}'")
                evidence.append(
                    _snippet_to_evidence(
                        snippet,
                        source_kind="symbol_definition",
                        file_path=file_path,
                        symbol=symbol,
                        request_kind="symbol_definition",
                        rationale=f"Definition lookup for symbol {symbol}",
                    )
                )
                break
        if len(evidence) >= MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST:
            break
    return evidence


def search_symbol_usages(clone_path: Path, symbol: str) -> list[EvidenceItem]:
    if not symbol:
        return []

    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    evidence: list[EvidenceItem] = []
    for file_path in list_python_files(clone_path):
        content = read_file(clone_path, file_path)
        if not content:
            continue
        lines = content.splitlines()
        matches = [idx for idx, line in enumerate(lines, 1) if pattern.search(line)]
        if not matches:
            continue
        idx = matches[0]
        start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
        end = min(len(lines), idx + MAX_STAGE2_SNIPPET_WINDOW)
        snippet = _make_snippet(file_path, lines, start, end, f"Usage of symbol '{symbol}'")
        evidence.append(
            _snippet_to_evidence(
                snippet,
                source_kind="symbol_usage",
                file_path=file_path,
                symbol=symbol,
                request_kind="symbol_usage",
                rationale=f"Usage lookup for symbol {symbol}",
            )
        )
        if len(evidence) >= MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST:
            break
    return evidence


def load_file_context(clone_path: Path, file_path: str) -> list[EvidenceItem]:
    content = read_file(clone_path, file_path)
    if content is None:
        return []
    snippet = ContextSnippet(
        label=f"Requested file '{file_path}'",
        content=f"File: {file_path}\n```\n{add_line_numbers(content)}\n```",
    )
    return [
        _snippet_to_evidence(
            snippet,
            source_kind="file",
            file_path=file_path,
            symbol=None,
            request_kind="file",
            rationale=f"Full file requested for {file_path}",
        )
    ]


def resolve_import_context(clone_path: Path, symbol: str) -> list[EvidenceItem]:
    """Resolve import statements for a symbol and include target definitions when possible."""
    if not symbol:
        return []

    evidence: list[EvidenceItem] = []
    for file_path in list_python_files(clone_path):
        content = read_file(clone_path, file_path)
        if not content:
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        lines = content.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    alias_name = alias.asname or alias.name.split(".")[-1]
                    if alias_name != symbol:
                        continue
                    idx = getattr(node, "lineno", 1)
                    start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
                    end = min(len(lines), idx + MAX_STAGE2_SNIPPET_WINDOW)
                    snippet = _make_snippet(file_path, lines, start, end, f"Import resolution for '{symbol}'")
                    evidence.append(
                        _snippet_to_evidence(
                            snippet,
                            source_kind="import_resolution",
                            file_path=file_path,
                            symbol=symbol,
                            request_kind="import_resolution",
                            rationale=f"Resolved import site for {symbol}",
                        )
                    )
                    target_symbol = alias.name.split(".")[-1]
                    if target_symbol and target_symbol != symbol:
                        evidence.extend(search_symbol_definitions(clone_path, target_symbol))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    alias_name = alias.asname or alias.name.split(".")[-1]
                    if alias_name != symbol:
                        continue
                    idx = getattr(node, "lineno", 1)
                    start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
                    end = min(len(lines), idx + MAX_STAGE2_SNIPPET_WINDOW)
                    snippet = _make_snippet(file_path, lines, start, end, f"Import resolution for '{symbol}'")
                    evidence.append(
                        _snippet_to_evidence(
                            snippet,
                            source_kind="import_resolution",
                            file_path=file_path,
                            symbol=symbol,
                            request_kind="import_resolution",
                            rationale=f"Resolved import site for {symbol}",
                        )
                    )
        if len(evidence) >= MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST:
            break
    return evidence[:MAX_STAGE2_SNIPPET_RESULTS_PER_REQUEST]


def resolve_class_method_definition(clone_path: Path, class_name: str, method_name: str) -> list[EvidenceItem]:
    if not class_name or not method_name:
        return []

    evidence: list[EvidenceItem] = []
    for file_path in list_python_files(clone_path):
        content = read_file(clone_path, file_path)
        if not content:
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        lines = content.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                        idx = getattr(child, "lineno", getattr(node, "lineno", 1))
                        end_lineno = getattr(child, "end_lineno", idx)
                        start = max(1, idx - MAX_STAGE2_SNIPPET_WINDOW)
                        end = min(len(lines), end_lineno + MAX_STAGE2_SNIPPET_WINDOW)
                        snippet = _make_snippet(
                            file_path,
                            lines,
                            start,
                            end,
                            f"Method definition for '{class_name}.{method_name}'",
                        )
                        evidence.append(
                            _snippet_to_evidence(
                                snippet,
                                source_kind="class_method_definition",
                                file_path=file_path,
                                symbol=f"{class_name}.{method_name}",
                                request_kind="class_method_definition",
                                rationale=f"Class method lookup for {class_name}.{method_name}",
                            )
                        )
                        return evidence
    return evidence


def load_dependency_manifests(clone_path: Path, dependency_name: str = "") -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []

    manifest_paths = list(_DEPENDENCY_MANIFEST_CANDIDATES)
    requirements_dir = clone_path / "requirements"
    if requirements_dir.exists() and requirements_dir.is_dir():
        for path in sorted(requirements_dir.glob("*.txt")):
            manifest_paths.append(path.relative_to(clone_path).as_posix())

    lowered_name = dependency_name.lower().strip()
    for rel_path in manifest_paths:
        content = read_file(clone_path, rel_path)
        if content is None:
            continue
        if lowered_name and lowered_name not in content.lower():
            continue
        snippet = ContextSnippet(
            label=f"Dependency manifest '{rel_path}'",
            content=f"File: {rel_path}\n```\n{content}\n```",
        )
        evidence.append(
            _snippet_to_evidence(
                snippet,
                source_kind="dependency_manifest",
                file_path=rel_path,
                symbol=dependency_name or None,
                request_kind="dependency_manifest",
                rationale=f"Dependency manifest lookup for {dependency_name or 'all dependencies'}",
            )
        )
        if len(evidence) >= MAX_STAGE2_DEPENDENCY_CONTEXT_FILES:
            break
    return evidence


def resolve_requests(clone_path: Path, requests: list[EvidenceRequest]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    seen: set[tuple[str, str | None, int | None, int | None]] = set()

    for request in requests:
        if request.kind == "symbol_definition":
            resolved = search_symbol_definitions(clone_path, request.symbol)
        elif request.kind == "symbol_usage":
            resolved = search_symbol_usages(clone_path, request.symbol)
        elif request.kind == "file":
            resolved = load_file_context(clone_path, request.file_path)
        elif request.kind == "import_resolution":
            resolved = resolve_import_context(clone_path, request.symbol)
        elif request.kind == "class_method_definition":
            resolved = resolve_class_method_definition(clone_path, request.class_name, request.method_name)
        elif request.kind == "dependency_manifest":
            resolved = load_dependency_manifests(clone_path, request.dependency_name)
        else:
            resolved = []

        for item in resolved:
            key = (item.label, item.file_path, item.line_start, item.line_end)
            if key in seen:
                continue
            seen.add(key)
            if request.why and not item.rationale:
                item.rationale = request.why
            evidence.append(item)
    return evidence
