"""Immutable run state and durable, digest-linked events using the standard library."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Mapping, Optional, Tuple
from uuid import UUID


class RunStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    PAUSED = "paused"
    BLOCKED = "blocked"


class GateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


_SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                      / "docs/agent/STATE_SCHEMA.json").read_text(encoding="utf-8"))
_PROPERTIES = _SCHEMA["properties"]
_GATE_SCHEMA = _PROPERTIES["gates"]["additionalProperties"]
_TIMESTAMP = _PROPERTIES["createdAt"]
_DIGEST = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_EVENT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["type", "payload", "createdAt", "previousDigest", "digest"],
    "properties": {
        "type": {"type": "string", "minLength": 1},
        "payload": {"type": "object"}, "createdAt": _TIMESTAMP,
        "previousDigest": {"anyOf": [_DIGEST, {"type": "null"}]},
        "digest": _DIGEST,
    },
}


def _matches(value, schema):
    try:
        _validate(value, schema)
    except ValueError:
        return False
    return True


def _validate(value, schema):
    """Validate the JSON Schema subset used by the versioned state contract."""
    if "anyOf" in schema and not any(_matches(value, item) for item in schema["anyOf"]):
        raise ValueError("Value does not match any allowed schema")
    kind = schema.get("type")
    valid_type = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": type(value) is int,
        "null": value is None,
    }
    if kind is not None and not valid_type.get(kind, False):
        raise ValueError("Invalid JSON value type")
    if "const" in schema and value != schema["const"]:
        raise ValueError("Unsupported constant or schema version")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Unknown status")
    if isinstance(value, dict):
        if not set(schema.get("required", ())) <= value.keys():
            raise ValueError("Missing required fields")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if "propertyNames" in schema:
                _validate(key, schema["propertyNames"])
            if key in properties:
                _validate(item, properties[key])
            elif additional is False:
                raise ValueError("Unknown fields")
            elif isinstance(additional, dict):
                _validate(item, additional)
    elif isinstance(value, list):
        if schema.get("uniqueItems") and any(item in value[:index]
                                             for index, item in enumerate(value)):
            raise ValueError("Duplicate array items")
        for item in value:
            _validate(item, schema.get("items", {}))
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError("Empty string")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError("String does not match contract")
        if schema.get("format") == "date-time":
            _parse_timestamp(value)
        elif schema.get("format") == "uuid" and str(UUID(value)) != value:
            raise ValueError("Invalid UUID format")
    elif type(value) is int and value < schema.get("minimum", value):
        raise ValueError("Integer below minimum")
    for item in schema.get("allOf", ()):
        _validate(value, item)
    if "if" in schema and _matches(value, schema["if"]):
        _validate(value, schema.get("then", {}))


def _parse_timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid timestamp") from error


def _timestamp(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _read_json(content):
    try:
        return json.loads(content, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Malformed JSON") from error


def _json_value(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("Unsupported JSON value")


def _canonical(value):
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class AttemptState:
    total: int
    by_fingerprint: Mapping[str, int]

    def __post_init__(self):
        object.__setattr__(self, "by_fingerprint", MappingProxyType(dict(self.by_fingerprint)))
        _validate({"total": self.total, "byFingerprint": dict(self.by_fingerprint)},
                  _PROPERTIES["attempts"])


@dataclass(frozen=True)
class GateEvidence:
    gate_id: str
    status: GateStatus
    exit_code: Optional[int]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    evidence_path: Optional[Path]
    head_commit: str

    def __post_init__(self):
        object.__setattr__(self, "status", GateStatus(self.status))
        if self.evidence_path is not None:
            object.__setattr__(self, "evidence_path", Path(self.evidence_path))
        _validate(_gate_document(self), _GATE_SCHEMA)


def _gate_document(gate):
    return {
        "gateId": gate.gate_id, "status": gate.status.value,
        "exitCode": gate.exit_code,
        "startedAt": _timestamp(gate.started_at) if gate.started_at is not None else None,
        "endedAt": _timestamp(gate.ended_at) if gate.ended_at is not None else None,
        "evidencePath": gate.evidence_path.as_posix() if gate.evidence_path is not None else None,
        "headCommit": gate.head_commit,
    }


@dataclass(frozen=True)
class RunState:
    schema_version: int
    run_id: str
    plan_path: Path
    plan_sha256: str
    worktree_path: Path
    branch: str
    base_commit: str
    head_commit: str
    milestone_id: str
    milestone_title: str
    status: RunStatus
    attempts: AttemptState
    last_failure_fingerprint: Optional[str]
    required_gates: Tuple[str, ...]
    gates: Mapping[str, GateEvidence]
    changed_paths: Tuple[Path, ...]
    decisions: Tuple[Mapping[str, object], ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "status", RunStatus(self.status))
        object.__setattr__(self, "plan_path", Path(self.plan_path))
        object.__setattr__(self, "worktree_path", Path(self.worktree_path))
        object.__setattr__(self, "required_gates", tuple(self.required_gates))
        object.__setattr__(self, "gates", MappingProxyType(dict(self.gates)))
        object.__setattr__(self, "changed_paths", tuple(Path(path) for path in self.changed_paths))
        decisions = []
        for decision in self.decisions:
            copied = _json_value(decision)
            _validate(copied, _PROPERTIES["decisions"]["items"])
            decisions.append(MappingProxyType(copied))
        object.__setattr__(self, "decisions", tuple(decisions))
        _validate_state_document(_state_document(self))


def _state_document(state):
    return {
        "schemaVersion": state.schema_version, "runId": state.run_id,
        "planPath": state.plan_path.as_posix(), "planSha256": state.plan_sha256,
        "worktreePath": state.worktree_path.as_posix(), "branch": state.branch,
        "baseCommit": state.base_commit, "headCommit": state.head_commit,
        "milestoneId": state.milestone_id, "milestoneTitle": state.milestone_title,
        "status": state.status.value,
        "attempts": {"total": state.attempts.total,
                     "byFingerprint": dict(state.attempts.by_fingerprint)},
        "lastFailureFingerprint": state.last_failure_fingerprint,
        "requiredGates": list(state.required_gates),
        "gates": {key: _gate_document(gate) for key, gate in state.gates.items()},
        "changedPaths": [path.as_posix() for path in state.changed_paths],
        "decisions": [dict(decision) for decision in state.decisions],
        "createdAt": _timestamp(state.created_at), "updatedAt": _timestamp(state.updated_at),
    }


def _validate_state_document(document):
    _validate(document, _SCHEMA)
    if document["status"] != RunStatus.COMPLETED.value:
        return
    for gate_id in document["requiredGates"]:
        gate = document["gates"].get(gate_id)
        if (gate is None or gate["gateId"] != gate_id
                or gate["status"] != GateStatus.PASSED.value
                or gate["headCommit"] != document["headCommit"]):
            raise ValueError("Required gate has no passing evidence at current HEAD")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_state(path: Path) -> RunState:
    document = _read_json(path.read_bytes())
    _validate_state_document(document)
    gates = {}
    for key, item in document["gates"].items():
        gates[key] = GateEvidence(
            item["gateId"], GateStatus(item["status"]), item["exitCode"],
            _parse_timestamp(item["startedAt"]) if item["startedAt"] is not None else None,
            _parse_timestamp(item["endedAt"]) if item["endedAt"] is not None else None,
            Path(item["evidencePath"]) if item["evidencePath"] is not None else None,
            item["headCommit"],
        )
    return RunState(
        document["schemaVersion"], document["runId"], Path(document["planPath"]),
        document["planSha256"], Path(document["worktreePath"]), document["branch"],
        document["baseCommit"], document["headCommit"], document["milestoneId"],
        document["milestoneTitle"], RunStatus(document["status"]),
        AttemptState(document["attempts"]["total"], document["attempts"]["byFingerprint"]),
        document["lastFailureFingerprint"], tuple(document["requiredGates"]), gates,
        tuple(Path(item) for item in document["changedPaths"]), tuple(document["decisions"]),
        _parse_timestamp(document["createdAt"]), _parse_timestamp(document["updatedAt"]),
    )


def save_state_atomic(path: Path, state: RunState) -> None:
    document = _state_document(state)
    _validate_state_document(document)
    content = _canonical(document) + b"\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


_TRANSITIONS = {
    RunStatus.PLANNED: {RunStatus.ACTIVE, RunStatus.PAUSED, RunStatus.BLOCKED},
    RunStatus.ACTIVE: {RunStatus.VERIFYING, RunStatus.PAUSED, RunStatus.BLOCKED},
    RunStatus.VERIFYING: {RunStatus.REPAIRING, RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.BLOCKED},
    RunStatus.REPAIRING: {RunStatus.VERIFYING, RunStatus.PAUSED, RunStatus.BLOCKED},
    RunStatus.COMPLETED: set(), RunStatus.PAUSED: set(), RunStatus.BLOCKED: set(),
}


def transition(state: RunState, target: RunStatus, reason: str) -> RunState:
    target = RunStatus(target)
    if target not in _TRANSITIONS[state.status]:
        raise ValueError("Illegal run status transition")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Transition requires a reason")
    now = datetime.now(timezone.utc)
    decision = {"type": "transition", "summary": reason, "createdAt": _timestamp(now)}
    return replace(state, status=target, updated_at=now,
                   decisions=state.decisions + (decision,))


def read_events(path: Path) -> list[dict[str, object]]:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return []
    if not content:
        return []
    if not content.endswith(b"\n"):
        raise ValueError("Truncated final event line")
    records = []
    previous = None
    for line in content.split(b"\n")[:-1]:
        record = _read_json(line)
        _validate(record, _EVENT_SCHEMA)
        if not record["type"].strip():
            raise ValueError("Empty event type")
        if record["previousDigest"] != previous:
            raise ValueError("Broken event chain")
        body = {key: value for key, value in record.items() if key != "digest"}
        if record["digest"] != hashlib.sha256(_canonical(body)).hexdigest():
            raise ValueError("Event digest mismatch")
        records.append(record)
        previous = record["digest"]
    return records


def append_event(path: Path, event_type: str, payload: Mapping[str, object]) -> str:
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("Event type must be non-empty")
    if not isinstance(payload, Mapping):
        raise ValueError("Event payload must be an object")
    body = {
        "type": event_type, "payload": _json_value(payload),
        "createdAt": _timestamp(datetime.now(timezone.utc)), "previousDigest": None,
    }
    records = read_events(path)
    if records:
        body["previousDigest"] = records[-1]["digest"]
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    record = dict(body, digest=digest)
    _validate(record, _EVENT_SCHEMA)
    content = _canonical(record) + b"\n"
    with path.open("ab") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return digest
