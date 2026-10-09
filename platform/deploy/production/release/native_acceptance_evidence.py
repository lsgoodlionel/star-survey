#!/usr/bin/env python3
"""Create allowlisted, secret-scanned native acceptance evidence."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import quote, quote_plus


REDACTED = "[REDACTED]"
SENSITIVE_TERMS = ("authorization", "password", "passwd", "secret", "token", "privatekey")
AUTHORIZATION = re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;}]+")
AUTH_CREDENTIAL = re.compile(r"(?i)\b((?:bearer|basic)\s+)(?!\[REDACTED\])[^\s,;}]+")
URI_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)[^\s/@]+(@)")
QUERY_SECRET = re.compile(r"(?i)([?&](?:password|passwd|secret|token|private[_-]?key|authorization)=)[^&#\s]+")
INLINE_SECRET = re.compile(
    r'''(?ix)
    (
      ["']?(?:password|passwd|secret|token|private[_-]?key|authorization)["']?
      \s*[:=]\s*
    )
    (?:["'][^"']*["']|[^\s,;}]+)
    ''',
)


class EvidenceError(ValueError):
    pass


def normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def sensitive_key(value: object) -> bool:
    key = normalized_key(value)
    return any(term in key for term in SENSITIVE_TERMS)


def secret_variants(secret_values: Iterable[str]) -> set[str]:
    variants: set[str] = set()
    for value in secret_values:
        if not value:
            continue
        variants.update({value, quote(value, safe=""), quote_plus(value)})
        variants.add(base64.b64encode(value.encode("utf-8")).decode("ascii"))
    return {value for value in variants if len(value) >= 4}


def sanitize_string(value: str, secret_values: Iterable[str] = ()) -> str:
    sanitized = value
    for secret in sorted(secret_variants(secret_values), key=len, reverse=True):
        sanitized = sanitized.replace(secret, REDACTED)
    sanitized = AUTHORIZATION.sub(r"\1" + REDACTED, sanitized)
    sanitized = AUTH_CREDENTIAL.sub(r"\1" + REDACTED, sanitized)
    sanitized = URI_USERINFO.sub(r"\1" + REDACTED + r"\2", sanitized)
    sanitized = QUERY_SECRET.sub(r"\1" + REDACTED, sanitized)
    sanitized = INLINE_SECRET.sub(r"\1" + REDACTED, sanitized)
    return sanitized


def sanitize(value: Any, secret_values: Iterable[str] = ()) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTED if sensitive_key(key) else sanitize(child, secret_values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, secret_values) for item in value]
    if isinstance(value, str):
        return sanitize_string(value, secret_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return REDACTED


def load_secret_values(directory: Path | None) -> set[str]:
    if directory is None or not directory.is_dir() or directory.is_symlink():
        return set()
    values: set[str] = set()
    for path in directory.iterdir():
        if path.is_file() and not path.is_symlink():
            try:
                value = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                continue
            if value:
                values.add(value)
    return values


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_doctor_evidence(report: dict[str, Any], output: Path, secret_values: Iterable[str] = ()) -> None:
    if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
        raise EvidenceError("doctor output is not structured JSON")
    checks = []
    for item in report["checks"]:
        if not isinstance(item, dict):
            raise EvidenceError("doctor check is not an object")
        checks.append(sanitize({
            "id": item.get("id"),
            "status": item.get("status"),
            "summary": item.get("summary", ""),
        }, secret_values))
    write_json(output, {
        "schemaVersion": 1,
        "status": report.get("status"),
        "checks": checks,
    })


def write_service_evidence(lines: str, output: Path, secret_values: Iterable[str] = ()) -> None:
    services = []
    for line in lines.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError("container evidence is not JSON") from exc
        if not isinstance(item, dict):
            raise EvidenceError("container evidence item is not an object")
        services.append(sanitize({key: item.get(key, "") for key in ("id", "image", "name", "state", "health")}, secret_values))
    write_json(output, {"schemaVersion": 1, "services": services})


def suspicious(value: Any, variants: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if sensitive_key(key) and child != REDACTED:
                return True
            if suspicious(child, variants):
                return True
        return False
    if isinstance(value, list):
        return any(suspicious(item, variants) for item in value)
    if not isinstance(value, str):
        return False
    if any(secret in value for secret in variants):
        return True
    return sanitize_string(value) != value


def scan_directory(directory: Path, secret_values: Iterable[str] = ()) -> None:
    variants = secret_variants(secret_values)
    allowed_names = {"summary.json", "preflight.json", "services.json"}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise EvidenceError(f"unexpected evidence artifact: {path.name}")
        if path.name not in allowed_names and not path.name.startswith("doctor-"):
            raise EvidenceError(f"evidence filename is not allowlisted: {path.name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(f"evidence is not valid JSON: {path.name}") from exc
        if suspicious(value, variants):
            raise EvidenceError(f"possible secret remains in evidence: {path.name}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--secrets-dir", type=Path)
    sub = result.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--input", type=Path, required=True)
    doctor.add_argument("--output", type=Path, required=True)
    services = sub.add_parser("services")
    services.add_argument("--input", type=Path, required=True)
    services.add_argument("--output", type=Path, required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--directory", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    secrets = load_secret_values(args.secrets_dir)
    try:
        if args.command == "doctor":
            report = json.loads(args.input.read_text(encoding="utf-8"))
            write_doctor_evidence(report, args.output, secrets)
        elif args.command == "services":
            write_service_evidence(args.input.read_text(encoding="utf-8"), args.output, secrets)
        else:
            scan_directory(args.directory, secrets)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, EvidenceError) as exc:
        print(f"native acceptance evidence failed: {exc}", file=__import__("sys").stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
