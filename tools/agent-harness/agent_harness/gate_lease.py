"""Atomic repository-local leases for one-process-per-EvidenceKey execution."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
from uuid import uuid4

from .gate_summary import GateSummary
from .state import validate_document


_DIGEST = re.compile(r"[0-9a-f]{64}")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SCHEMA = json.loads((Path(__file__).resolve().parents[3]
                      / "docs/agent/GATE_SUMMARY_SCHEMA.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class GateLease:
    key: str
    owner: str
    path: Path
    acquired: bool
    expires_at: datetime


def _cache_root(value: Path) -> Path:
    root = Path(value)
    path = root.parent
    if root.is_symlink() or path.is_symlink():
        raise ValueError("Lease storage cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    leases = root / ".leases"
    if leases.is_symlink():
        raise ValueError("Lease storage cannot be a symlink")
    leases.mkdir(exist_ok=True)
    return root


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z") or len(value) > 64:
        raise ValueError("Invalid lease timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Invalid lease timestamp")
    return result


def _atomic_json(path: Path, document: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _valid_completed_record(root: Path, key: str) -> bool:
    try:
        record = json.loads((root / key / "record.json").read_text(encoding="utf-8"))
        return (record.get("version") == 1 and record.get("key") == key
                and record.get("status") == "passed" and record.get("exitCode") == 0
                and re.fullmatch(r"[0-9a-f]{40}", record.get("originalHead", "")) is not None
                and _DIGEST.fullmatch(record.get("summarySha256", "")) is not None)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _existing_lease(path: Path, fallback_ttl: int) -> tuple[str, datetime]:
    try:
        document = json.loads((path / "owner.json").read_text(encoding="utf-8"))
        if set(document) != {"version", "key", "owner", "host", "pid", "createdAt", "expiresAt"}:
            raise ValueError()
        return document["owner"], _parse_timestamp(document["expiresAt"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        expiry = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) + timedelta(
            seconds=fallback_ttl
        )
        return "unknown-owner", expiry


def acquire_gate_lease(cache_root: Path, key: str, owner: str,
                       ttl_seconds: int) -> GateLease:
    if (_DIGEST.fullmatch(key or "") is None or _OWNER.fullmatch(owner or "") is None
            or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400):
        raise ValueError("Invalid Gate lease request")
    root = _cache_root(cache_root)
    path = root / ".leases" / key
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    while True:
        try:
            path.mkdir()
        except FileExistsError:
            current_owner, current_expiry = _existing_lease(path, ttl_seconds)
            if current_expiry > now or _valid_completed_record(root, key):
                return GateLease(key, current_owner, path, False, current_expiry)
            stale = path.with_name(".stale-" + key + "-" + uuid4().hex)
            try:
                os.rename(path, stale)
            except (FileNotFoundError, FileExistsError):
                continue
            shutil.rmtree(stale, ignore_errors=True)
            continue
        document = {
            "version": 1, "key": key, "owner": owner,
            "host": socket.gethostname()[:128], "pid": os.getpid(),
            "createdAt": _timestamp(now), "expiresAt": _timestamp(expires),
        }
        try:
            _atomic_json(path / "owner.json", document)
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        return GateLease(key, owner, path, True, expires)


def release_gate_lease(lease: GateLease) -> None:
    if not lease.acquired or not lease.path.exists():
        return
    try:
        document = json.loads((lease.path / "owner.json").read_text(encoding="utf-8"))
        if document.get("owner") != lease.owner or document.get("key") != lease.key:
            return
        stale = lease.path.with_name(".released-" + lease.key + "-" + uuid4().hex)
        os.rename(lease.path, stale)
        shutil.rmtree(stale, ignore_errors=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def wait_for_gate_summary(cache_root: Path, key: str,
                          timeout_seconds: int) -> GateSummary:
    if (_DIGEST.fullmatch(key or "") is None or type(timeout_seconds) is not int
            or timeout_seconds <= 0):
        raise ValueError("Invalid Gate summary wait")
    root = _cache_root(cache_root)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            document = json.loads((root / key / "summary.json").read_text(encoding="utf-8"))
            validate_document(document, _SCHEMA)
            return GateSummary(
                document["schemaVersion"], document["gateId"], document["status"],
                document["exitCode"], document["durationMs"], document["counts"],
                document["evidenceKey"], document["headCommit"], document["diagnostic"],
                document["source"], document["reused"],
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            time.sleep(0.02)
    raise TimeoutError("Timed out waiting for Gate summary")
