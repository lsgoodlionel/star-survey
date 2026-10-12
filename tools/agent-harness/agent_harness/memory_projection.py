"""One-way, marker-bounded projection of verified Harness state to Obsidian."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

from .token_report import TokenReport


class MemoryProjectionError(ValueError):
    """Projection configuration or machine-owned note content is unsafe."""


@dataclass(frozen=True)
class ProjectionTarget:
    projection_id: str
    note_path: Path
    start_marker: str
    end_marker: str


@dataclass(frozen=True)
class ProjectionConfig:
    repo_root: Path
    vault_root: Path
    host_path: Path
    targets: tuple[ProjectionTarget, ...]
    journal: Mapping[str, object]


@dataclass(frozen=True)
class Projection:
    source_digest: str
    markdown: str


@dataclass(frozen=True)
class MemorySyncOperation:
    projection_id: str
    target: Path
    content: str | None
    block_digest: str
    status: str


@dataclass(frozen=True)
class MemorySyncPlan:
    config: ProjectionConfig
    projection: Projection
    operations: tuple[MemorySyncOperation, ...]


@dataclass(frozen=True)
class MemorySyncResult:
    updated: tuple[str, ...]
    pending: tuple[str, ...]


def memory_status(config: ProjectionConfig) -> dict[str, object]:
    entries = config.journal.get("projections", {})
    if not isinstance(entries, Mapping):
        raise MemoryProjectionError("Projection journal is invalid")
    pending = sorted(key for key, value in entries.items()
                     if isinstance(value, Mapping) and value.get("status") == "pending")
    synced = sorted(key for key, value in entries.items()
                    if isinstance(value, Mapping) and value.get("status") == "synced")
    return {"configured": True, "vaultAvailable": config.vault_root.is_dir(),
            "pending": pending, "synced": synced}


_HOST_RELATIVE = Path("var/agent-harness/host-memory.json")
_CONFIG_RELATIVE = Path("docs/agent/MEMORY_PROJECTIONS.json")
_METADATA = re.compile(
    r"^<!-- codex-projection:v1 source=([0-9a-f]{64}) body=([0-9a-f]{64}) -->$"
)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MemoryProjectionError("Memory projection configuration is unavailable") from error
    if not isinstance(value, dict):
        raise MemoryProjectionError("Memory projection configuration must be an object")
    return value


def configure_memory(repo_root: Path, vault_root: Path) -> Path:
    repo = Path(repo_root).resolve(strict=True)
    vault = Path(vault_root).expanduser()
    if not vault.is_absolute():
        raise MemoryProjectionError("Vault root must be an absolute local path")
    vault = vault.resolve(strict=False)
    host_path = repo / _HOST_RELATIVE
    previous = {}
    if host_path.is_file():
        previous = _read_object(host_path)
    projections = previous.get("projections", {}) if previous.get("vaultRoot") == str(vault) else {}
    document = {"version": 1, "vaultRoot": str(vault), "projections": projections}
    _atomic_text(host_path, json.dumps(document, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")) + "\n")
    return host_path


def _relative_note(value) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MemoryProjectionError("Projection note path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise MemoryProjectionError("Projection note path must be relative to the vault")
    return path


def load_projection_config(repo_root: Path) -> ProjectionConfig:
    repo = Path(repo_root).resolve(strict=True)
    document = _read_object(repo / _CONFIG_RELATIVE)
    if (set(document) not in ({"version", "projections"},
                              {"version", "machineBlockVersion", "projections"})
            or document["version"] != 1
            or document.get("machineBlockVersion", 1) != 1):
        raise MemoryProjectionError("Unsupported projection configuration")
    rows = document.get("projections")
    if not isinstance(rows, list) or not rows:
        raise MemoryProjectionError("At least one projection is required")
    targets = []
    identifiers = set()
    note_paths = set()
    markers_seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "id", "notePath", "startMarker", "endMarker"}:
            raise MemoryProjectionError("Projection entry is invalid")
        identifier = row["id"]
        markers = (row["startMarker"], row["endMarker"])
        if (not isinstance(identifier, str)
                or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier) is None
                or identifier in identifiers
                or any(not isinstance(item, str) or "\n" in item or not item for item in markers)
                or markers[0] == markers[1]):
            raise MemoryProjectionError("Projection identifier or markers are invalid")
        identifiers.add(identifier)
        note_path = _relative_note(row["notePath"])
        if note_path in note_paths or any(marker in markers_seen for marker in markers):
            raise MemoryProjectionError("Projection notes and markers must be unique")
        note_paths.add(note_path)
        markers_seen.update(markers)
        targets.append(ProjectionTarget(identifier, note_path,
                                        markers[0], markers[1]))
    host_path = repo / _HOST_RELATIVE
    host = _read_object(host_path)
    if (set(host) != {"version", "vaultRoot", "projections"} or host["version"] != 1
            or not isinstance(host["vaultRoot"], str)
            or not isinstance(host["projections"], dict)):
        raise MemoryProjectionError("Host memory configuration is invalid")
    vault = Path(host["vaultRoot"])
    if not vault.is_absolute():
        raise MemoryProjectionError("Configured vault root is not absolute")
    return ProjectionConfig(repo, vault, host_path, tuple(targets), host)


def _value(value):
    return value.value if hasattr(value, "value") else str(value)


def render_projection(state, delivery: Mapping[str, object],
                      token_report: TokenReport) -> Projection:
    gates = sorted(f"{identifier}={_value(gate.status)}" for identifier, gate in state.gates.items())
    delivery_status = delivery.get("deliveryStatus", "unknown")
    source = {
        "runId": state.run_id, "branch": state.branch,
        "headCommit": state.head_commit, "status": _value(state.status),
        "milestoneId": state.milestone_id,
        "updatedAt": state.updated_at.isoformat(), "gates": gates,
        "deliveryStatus": delivery_status, "tokenReport": token_report.to_dict(),
    }
    source_digest = _digest(_canonical(source))
    completed = "当前 Milestone 已完成" if _value(state.status) == "completed" else "已验证状态已投影"
    remaining = "无" if _value(state.status) == "completed" else "继续当前 Milestone，完成后重新同步"
    gate_summary = "、".join(gates) if gates else "尚无 Gate 证据"
    markdown = (
        "### Codex 已验证进度\n\n"
        f"- 日期：`{state.updated_at.isoformat()}`\n"
        f"- 分支：`{state.branch}`\n"
        f"- 提交：`{state.head_commit}`\n"
        f"- Harness run：`{state.run_id}`\n"
        f"- Milestone：`{state.milestone_id}`（{_value(state.status)}）\n"
        f"- 测试摘要：{gate_summary}\n"
        f"- 完成项：{completed}\n"
        f"- 遗留：{remaining}\n"
        f"- 下一步：{remaining}\n"
        f"- 交付状态：`{delivery_status}`\n"
        f"- Token：raw `{token_report.usage.raw_tokens}`，active "
        f"`{token_report.usage.active_tokens}`，review active "
        f"`{token_report.review_usage.active_tokens}`\n"
        f"- 来源摘要：`{source_digest}`\n"
    )
    return Projection(source_digest, markdown)


def _safe_target(vault: Path, relative: Path) -> Path:
    target = vault / relative
    current = vault
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise MemoryProjectionError("Projection target traverses a symbolic link")
    if not target.resolve(strict=False).is_relative_to(vault.resolve(strict=False)):
        raise MemoryProjectionError("Projection target escapes the vault")
    return target


def _machine_block(target: ProjectionTarget, projection: Projection):
    body_digest = _digest(projection.markdown)
    inner = (f"<!-- codex-projection:v1 source={projection.source_digest} "
             f"body={body_digest} -->\n{projection.markdown}")
    block = f"{target.start_marker}\n{inner}{target.end_marker}"
    return block, _digest(inner)


def _replace_plan(target: ProjectionTarget, note: str, projection: Projection,
                  previous: Mapping[str, object]):
    block, new_digest = _machine_block(target, projection)
    starts, ends = note.count(target.start_marker), note.count(target.end_marker)
    if starts != ends or starts > 1:
        raise MemoryProjectionError("Projection marker pair is malformed")
    if starts == 0:
        separator = "" if not note or note.endswith("\n") else "\n"
        return note + separator + block + "\n", new_digest, "update"
    start = note.index(target.start_marker)
    end = note.index(target.end_marker, start) + len(target.end_marker)
    existing = note[start:end]
    lines = existing.splitlines(keepends=True)
    if len(lines) < 3:
        raise MemoryProjectionError("Projection machine block is malformed")
    metadata = lines[1].rstrip("\r\n")
    match = _METADATA.fullmatch(metadata)
    if match is None:
        raise MemoryProjectionError("Projection metadata is malformed")
    body = "".join(lines[2:])
    if body.endswith(target.end_marker):
        body = body[:-len(target.end_marker)]
    if _digest(body) != match.group(2):
        raise MemoryProjectionError("Projection machine block was edited")
    current_inner = existing[len(target.start_marker) + 1:-len(target.end_marker)]
    current_digest = _digest(current_inner)
    recorded = previous.get("blockDigest") if isinstance(previous, Mapping) else None
    if recorded is not None and recorded != current_digest:
        raise MemoryProjectionError("Projection machine block conflicts with local journal")
    if current_digest == new_digest:
        return None, new_digest, "noop"
    return note[:start] + block + note[end:], new_digest, "update"


def plan_memory_sync(config: ProjectionConfig,
                     projection: Projection) -> MemorySyncPlan:
    operations = []
    entries = config.journal.get("projections", {})
    if not isinstance(entries, Mapping):
        raise MemoryProjectionError("Projection journal is invalid")
    vault_available = config.vault_root.is_dir()
    for target in config.targets:
        path = _safe_target(config.vault_root, target.note_path)
        block, block_digest = _machine_block(target, projection)
        if not vault_available:
            operations.append(MemorySyncOperation(target.projection_id, path, None,
                                                  block_digest, "pending"))
            continue
        try:
            note = path.read_text(encoding="utf-8") if path.exists() else ""
        except (OSError, UnicodeError) as error:
            raise MemoryProjectionError("Projection note is unavailable") from error
        content, digest, status = _replace_plan(
            target, note, projection, entries.get(target.projection_id, {}))
        operations.append(MemorySyncOperation(target.projection_id, path, content,
                                              digest, status))
    return MemorySyncPlan(config, projection, tuple(operations))


def apply_memory_sync(plan: MemorySyncPlan) -> MemorySyncResult:
    updated, pending = [], []
    journal = dict(plan.config.journal)
    entries = {key: dict(value) for key, value in journal.get("projections", {}).items()
               if isinstance(key, str) and isinstance(value, Mapping)}
    for operation in plan.operations:
        if operation.status == "pending":
            pending.append(operation.projection_id)
        elif operation.status == "update":
            if operation.content is None:
                raise MemoryProjectionError("Projection update has no content")
            _safe_target(plan.config.vault_root,
                         operation.target.relative_to(plan.config.vault_root))
            _atomic_text(operation.target, operation.content)
            updated.append(operation.projection_id)
        entries[operation.projection_id] = {
            "status": "pending" if operation.status == "pending" else "synced",
            "sourceDigest": plan.projection.source_digest,
            "blockDigest": operation.block_digest,
        }
    journal["projections"] = entries
    _atomic_text(plan.config.host_path,
                 json.dumps(journal, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n")
    return MemorySyncResult(tuple(updated), tuple(pending))
