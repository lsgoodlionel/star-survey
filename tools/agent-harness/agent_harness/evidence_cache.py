"""Repository-local, content-addressed cache for passing Gate summaries."""

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import tempfile
from types import MappingProxyType
from typing import Mapping, Optional

from .config import GateDefinition, validate_gate_id
from .gate_summary import GateSummary, write_gate_summary_atomic
from .state import GateEvidence, GateStatus, validate_document


_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_SENSITIVE = re.compile(
    r"(?:^|[-_/])(security|access|migration|production|release|browser|e2e|"
    r"backup|restore|mysql|pgsql|postgres|parity)(?:$|[-_/])", re.I,
)
_SUMMARY_SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                              / "docs/agent/GATE_SUMMARY_SCHEMA.json")
                             .read_text(encoding="utf-8"))


@dataclass(frozen=True)
class RuntimeIdentity:
    os_name: str
    architecture: str
    database: Optional[str]
    tool_versions: Mapping[str, str]

    def __post_init__(self):
        for value in (self.os_name, self.architecture):
            if (not isinstance(value, str) or not value.strip() or len(value) > 128
                    or "\x00" in value):
                raise ValueError("Runtime identity labels must be bounded text")
        if (self.database is not None
                and (not isinstance(self.database, str) or not self.database.strip()
                     or len(self.database) > 64 or "\x00" in self.database)):
            raise ValueError("Database identity must be bounded text")
        if not isinstance(self.tool_versions, Mapping) or not self.tool_versions:
            raise ValueError("At least one trusted tool version is required")
        tools = {}
        for name, version in self.tool_versions.items():
            if (_LABEL.fullmatch(name) is None or not isinstance(version, str)
                    or not version.strip() or len(version) > 128 or "\x00" in version):
                raise ValueError("Tool identities must be bounded lowercase labels")
            tools[name] = version
        object.__setattr__(self, "tool_versions", MappingProxyType(dict(sorted(tools.items()))))


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts):
        raise ValueError("Unsafe cache input pattern")
    return value


def _safe_cache_root(root: Path) -> Path:
    path = root
    for part in ("var", "agent-harness", "cache", "evidence"):
        path = path / part
        if path.is_symlink():
            raise ValueError("Cache directories cannot be symlinks")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _content_reusable(gate: GateDefinition) -> bool:
    identity = " ".join((gate.id, gate.cwd, *gate.command, *gate.input_patterns))
    return (gate.execution_tier == "local_fast" and gate.reuse_policy == "content"
            and _SENSITIVE.search(identity) is None)


def _git_head(root: Path) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                            capture_output=True, text=True, check=False)
    value = result.stdout.strip()
    if result.returncode or _COMMIT.fullmatch(value) is None:
        raise ValueError("HEAD-bound evidence requires a Git commit")
    return value


def _pattern_entries(root: Path, pattern: str) -> tuple[Path, ...]:
    pattern = _relative(pattern)
    if pattern == ".":
        raise ValueError("Content reuse requires explicit input patterns")
    matches = list(root.glob(pattern))
    if not matches:
        raise ValueError("Gate input pattern matched no paths")
    entries = set()
    for match in matches:
        entries.add(match)
        if match.is_dir() and not match.is_symlink():
            entries.update(match.rglob("*"))
    safe = []
    for entry in entries:
        relative = entry.relative_to(root)
        if relative.parts[:2] == ("var", "agent-harness") or ".git" in relative.parts:
            continue
        safe.append(entry)
    if not safe:
        raise ValueError("Gate input pattern has no cacheable paths")
    return tuple(sorted(safe, key=lambda item: item.relative_to(root).as_posix()))


def _entry_document(root: Path, path: Path) -> dict:
    relative = path.relative_to(root).as_posix()
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        return {"path": relative, "kind": "symlink", "mode": mode,
                "target": os.readlink(path)}
    if stat.S_ISDIR(info.st_mode):
        return {"path": relative, "kind": "directory", "mode": mode}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Special files cannot be Gate inputs")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": relative, "kind": "file", "mode": mode,
            "size": info.st_size, "sha256": digest.hexdigest()}


def compute_evidence_key(repo: Path, gate: GateDefinition,
                         runtime: RuntimeIdentity) -> str:
    root = Path(repo).resolve(strict=True)
    validate_gate_id(gate.id)
    if not isinstance(runtime, RuntimeIdentity):
        raise ValueError("A validated RuntimeIdentity is required")
    payload = {
        "version": 1,
        "repository": str(root),
        "gate": {
            "id": gate.id, "command": list(gate.command), "cwd": _relative(gate.cwd),
            "timeoutSeconds": gate.timeout_seconds,
            "executionTier": gate.execution_tier, "reusePolicy": gate.reuse_policy,
            "inputPatterns": list(gate.input_patterns),
            "summaryFormat": gate.summary_format,
        },
        "runtime": {
            "os": runtime.os_name, "architecture": runtime.architecture,
            "database": runtime.database, "tools": dict(runtime.tool_versions),
        },
    }
    if _content_reusable(gate):
        entries = {}
        for pattern in gate.input_patterns:
            for path in _pattern_entries(root, pattern):
                entries[path.relative_to(root).as_posix()] = _entry_document(root, path)
        payload["inputs"] = [entries[key] for key in sorted(entries)]
    else:
        payload["headCommit"] = _git_head(root)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Cache timestamps must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or not value.endswith("Z"):
        raise ValueError("Invalid cache timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Invalid cache timestamp")
    return parsed


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
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


def publish_cached_evidence(repo: Path, key: str,
                            evidence: GateEvidence) -> None:
    root = Path(repo).resolve(strict=True)
    if (_DIGEST.fullmatch(key or "") is None or evidence.status != GateStatus.PASSED
            or evidence.exit_code != 0 or evidence.evidence_key != key
            or evidence.summary_path is None or evidence.started_at is None
            or evidence.ended_at is None):
        raise ValueError("Only complete passing evidence can be cached")
    source_summary = (root / evidence.summary_path).resolve(strict=True)
    if not source_summary.is_relative_to(root):
        raise ValueError("Summary path escapes repository")
    summary_bytes = source_summary.read_bytes()
    summary = json.loads(summary_bytes)
    validate_document(summary, _SUMMARY_SCHEMA)
    if (summary["gateId"] != evidence.gate_id or summary["status"] != "passed"
            or summary["evidenceKey"] != key or summary["diagnostic"] is not None):
        raise ValueError("Summary does not match passing evidence")
    directory = _safe_cache_root(root) / key
    if directory.is_symlink():
        raise ValueError("Cache entry cannot be a symlink")
    directory.mkdir(exist_ok=True)
    cached_summary = directory / "summary.json"
    _atomic_bytes(cached_summary, summary_bytes)
    record = {
        "version": 1, "key": key, "gateId": evidence.gate_id,
        "status": "passed", "exitCode": 0,
        "startedAt": _timestamp(evidence.started_at),
        "endedAt": _timestamp(evidence.ended_at),
        "originalHead": evidence.head_commit,
        "summarySha256": hashlib.sha256(summary_bytes).hexdigest(),
    }
    _atomic_bytes(directory / "record.json", (
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8"))


def lookup_cached_evidence(repo: Path, key: str, gate: GateDefinition,
                           head_commit: str) -> Optional[GateEvidence]:
    try:
        root = Path(repo).resolve(strict=True)
        validate_gate_id(gate.id)
        if _DIGEST.fullmatch(key or "") is None or _COMMIT.fullmatch(head_commit or "") is None:
            return None
        directory = _safe_cache_root(root) / key
        if directory.is_symlink():
            return None
        record_path, summary_path = directory / "record.json", directory / "summary.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        required = {"version", "key", "gateId", "status", "exitCode", "startedAt",
                    "endedAt", "originalHead", "summarySha256"}
        if (set(record) != required or record["version"] != 1 or record["key"] != key
                or record["gateId"] != gate.id or record["status"] != "passed"
                or record["exitCode"] != 0
                or _COMMIT.fullmatch(record["originalHead"] or "") is None
                or (not _content_reusable(gate) and record["originalHead"] != head_commit)):
            return None
        summary_bytes = summary_path.read_bytes()
        if hashlib.sha256(summary_bytes).hexdigest() != record["summarySha256"]:
            return None
        document = json.loads(summary_bytes)
        validate_document(document, _SUMMARY_SCHEMA)
        if (document["gateId"] != gate.id or document["status"] != "passed"
                or document["evidenceKey"] != key or document["diagnostic"] is not None):
            return None
        reused_summary = GateSummary(
            1, gate.id, "passed", 0, document["durationMs"], document["counts"],
            key, head_commit, None, "reused", True,
        )
        derived = directory / ("summary-" + head_commit + ".json")
        write_gate_summary_atomic(derived, reused_summary)
        return GateEvidence(
            gate.id, GateStatus.PASSED, 0,
            _parse_timestamp(record["startedAt"]), _parse_timestamp(record["endedAt"]),
            record_path.relative_to(root), head_commit, derived.relative_to(root),
            key, "reused", True, 0,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
