"""Build small, deterministic and provenance-bound Codex context capsules."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
from typing import Optional, Tuple

from .state import RunState, validate_document


_SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                      / "docs/agent/CONTEXT_CAPSULE_SCHEMA.json").read_text(encoding="utf-8"))
_SECTIONS = {
    "AGENTS.md": ("实现与验证", "安全与权限", "自治执行与停止条件"),
    "docs/agent/AUTONOMY.md": ("修复预算与人工升级",),
    "docs/agent/PROJECT_MEMORY.md": ("身份与权威入口", "已确认边界与维护"),
}


@dataclass(frozen=True)
class ContextSource:
    path: str
    start_line: int
    end_line: int
    digest: str
    byte_count: int
    reason: str
    source_type: str = "repository"


@dataclass(frozen=True)
class ContextManifest:
    sources: Tuple[ContextSource, ...]
    stable_bytes: int
    task_bytes: int
    diagnostic_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class ContextCapsule:
    schema_version: int
    capsule_digest: str
    stable_prefix: str
    task_delta: str
    diagnostic: Optional[str]
    total_bytes: int
    sources: Tuple[ContextSource, ...]
    manifest: ContextManifest


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\x00" in value or "\\" in value or path.is_absolute()
            or PureWindowsPath(value).drive or ".." in path.parts):
        raise ValueError("Context source path must be repository-relative")
    return path.as_posix()


def _section(path: Path, heading: str) -> tuple[str, int, int]:
    if not isinstance(heading, str) or not heading.strip() or len(heading) > 256:
        raise ValueError("Markdown heading must be bounded text")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.rstrip("\r\n"))
        if match and match.group(2) == heading:
            matches.append((index, len(match.group(1))))
    if len(matches) != 1:
        raise ValueError("Markdown section is missing or ambiguous")
    start, level = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    text = "".join(lines[start:end])
    if text and not text.endswith("\n"):
        text += "\n"
    return text, start + 1, end


def extract_markdown_section(path: Path, heading: str) -> str:
    return _section(Path(path), heading)[0]


def _source_document(source: ContextSource) -> dict:
    return {
        "sourceType": source.source_type, "path": source.path,
        "startLine": source.start_line, "endLine": source.end_line,
        "digest": source.digest, "byteCount": source.byte_count,
        "reason": source.reason,
    }


def _document(capsule: ContextCapsule) -> dict:
    return {
        "schemaVersion": capsule.schema_version,
        "capsuleDigest": capsule.capsule_digest,
        "stablePrefix": capsule.stable_prefix,
        "taskDelta": capsule.task_delta,
        "diagnostic": capsule.diagnostic,
        "totalBytes": capsule.total_bytes,
        "sources": [_source_document(source) for source in capsule.sources],
    }


def _digest_document(document: dict) -> str:
    body = dict(document)
    body["capsuleDigest"] = ""
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_context_capsule(repo: Path, state: RunState, task: str,
                          diagnostic: Optional[str] = None) -> ContextCapsule:
    root = Path(repo).resolve(strict=True)
    if not isinstance(state, RunState) or not isinstance(task, str) or not task.strip():
        raise ValueError("Context Capsule requires state and a non-empty task")
    if diagnostic is not None and not isinstance(diagnostic, str):
        raise ValueError("Diagnostic must be text or null")
    fragments, source_rows = [], []
    for relative in sorted(_SECTIONS):
        safe = _safe_relative(relative)
        path = (root / safe).resolve(strict=True)
        if not path.is_relative_to(root) or path.is_symlink():
            raise ValueError("Context source escapes repository")
        for heading in _SECTIONS[relative]:
            text, start, end = _section(path, heading)
            encoded = text.encode("utf-8")
            fragments.append("Source: " + safe + "#" + heading + "\n" + text)
            source_rows.append(ContextSource(
                safe, start, end, hashlib.sha256(encoded).hexdigest(), len(encoded),
                "approved section: " + heading,
            ))
    stable = "\n".join(fragments)
    dynamic = json.dumps({
        "task": task,
        "run": {
            "runId": state.run_id, "milestoneId": state.milestone_id,
            "milestoneTitle": state.milestone_title, "branch": state.branch,
            "headCommit": state.head_commit,
            "requiredGates": list(state.required_gates),
            "remainingFailures": max(0, 5 - state.attempts.total),
        },
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    stable_bytes = len(stable.encode("utf-8"))
    task_bytes = len(dynamic.encode("utf-8"))
    diagnostic_bytes = len((diagnostic or "").encode("utf-8"))
    total = stable_bytes + task_bytes + diagnostic_bytes
    if stable_bytes > 12288 or task_bytes > 8192 or diagnostic_bytes > 4096 or total > 24576:
        raise ValueError("Context Capsule exceeds its byte budget")
    sources = tuple(sorted(source_rows, key=lambda item: (item.path, item.start_line)))
    manifest = ContextManifest(sources, stable_bytes, task_bytes, diagnostic_bytes, total)
    provisional = ContextCapsule(1, "0" * 64, stable, dynamic, diagnostic, total,
                                 sources, manifest)
    document = _document(provisional)
    digest = _digest_document(document)
    capsule = ContextCapsule(1, digest, stable, dynamic, diagnostic, total,
                             sources, manifest)
    validate_document(_document(capsule), _SCHEMA)
    return capsule


def render_context_capsule(capsule: ContextCapsule) -> str:
    document = _document(capsule)
    validate_document(document, _SCHEMA)
    if _digest_document(document) != capsule.capsule_digest:
        raise ValueError("Context Capsule digest mismatch")
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def validate_context_capsule_text(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65536:
        raise ValueError("Invalid Context Capsule text")
    document = json.loads(value)
    validate_document(document, _SCHEMA)
    if _digest_document(document) != document["capsuleDigest"]:
        raise ValueError("Context Capsule digest mismatch")
    expected = (len(document["stablePrefix"].encode("utf-8"))
                + len(document["taskDelta"].encode("utf-8"))
                + len((document["diagnostic"] or "").encode("utf-8")))
    if expected != document["totalBytes"]:
        raise ValueError("Context Capsule byte count mismatch")
    return document["capsuleDigest"]


def write_context_capsule_atomic(path: Path, capsule: ContextCapsule) -> None:
    path = Path(path)
    content = render_context_capsule(capsule) + "\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        validate_document(existing, _SCHEMA)
        if _digest_document(existing) != existing["capsuleDigest"]:
            raise ValueError("Existing Context Capsule was modified")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
