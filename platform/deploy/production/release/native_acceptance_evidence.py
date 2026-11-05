#!/usr/bin/env python3
"""Generate and validate strict native release acceptance evidence."""

from __future__ import annotations

import argparse
import base64
import csv
import io
import subprocess
import zipfile
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable
from urllib.parse import quote, quote_plus


REDACTED = "[REDACTED]"
MAX_EVIDENCE_BYTES = 256 * 1024
SENSITIVE_TERMS = (
    "authorization", "password", "passwd", "secret", "token", "privatekey",
    "apikey", "cookie", "setcookie",
)
HEADER_CREDENTIAL = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|"
    r"x-auth-token|x-access-token)\s*:\s*([^\r\n]+)"
)
AUTH_CREDENTIAL = re.compile(r"(?i)\b((?:bearer|basic)\s+)(?!\[REDACTED\])[^\s,;}]+")
PEM_BLOCK = re.compile(
    r"(?s)-----BEGIN [A-Z0-9 ][A-Z0-9 -]*-----.*?-----END [A-Z0-9 ][A-Z0-9 -]*-----"
)
URI_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)(?!\[REDACTED\])[^\s/@]+(@)")
QUERY_SECRET = re.compile(
    r"(?i)([?&](?:password|passwd|secret|token|private[_-]?key|authorization|api[_-]?key|cookie)=)"
    r"(?!\[REDACTED\])[^&#\s]+"
)
INLINE_SECRET = re.compile(
    r'''(?ix)
    (
      ["']?(?:password|passwd|secret|token|private[_-]?key|authorization|api[_-]?key|cookie)["']?
      \s*[:=]\s*
    )
    (?!\[REDACTED\])(?:["'][^"']*["']|[^\s,;}]+)
    ''',
)
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
DOCTOR_FILE = re.compile(r"doctor-(installed|upgraded|restored)\.json")
RUNNERS = {
    ("22.04", "amd64"),
    ("24.04", "amd64"),
    ("22.04", "arm64"),
    ("24.04", "arm64"),
}
MATRIX_EVIDENCE_KEYS = {
    ("22.04", "amd64"): "ubuntu-22.04-amd64",
    ("24.04", "amd64"): "ubuntu-24.04-amd64",
    ("22.04", "arm64"): "ubuntu-22.04-arm64",
    ("24.04", "arm64"): "ubuntu-24.04-arm64",
}


class EvidenceError(ValueError):
    pass


def require_clean_native_host() -> None:
    """Reject pre-existing project resources before a hosted native test starts.

    This is a preflight only: it does not authorize or perform deletion.
    """
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or os.environ.get('DOCKER_CONTEXT')):
        raise EvidenceError('native acceptance requires a GitHub-hosted runner')
    if os.environ.get('COMPOSE_PROJECT_NAME') not in (None, 'survey-production'):
        raise EvidenceError('native acceptance rejects a Compose project override')
    commands = (
        ['ps', '-aq', '--filter', 'label=com.docker.compose.project=survey-production'],
        ['ps', '-aq', '--filter', 'name=^/survey-production-'],
        ['volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=survey-production'],
        ['volume', 'ls', '-q', '--filter', 'name=^survey-production_'],
        ['network', 'ls', '-q', '--filter', 'label=com.docker.compose.project=survey-production'],
        ['network', 'ls', '-q', '--filter', 'name=^survey-production_'],
    )
    for command in commands:
        try:
            result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', *command],
                                    check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EvidenceError('native host resource preflight failed') from exc
        if result.stdout.strip():
            raise EvidenceError('native host already contains production-project resources')


def require_no_restore_resources() -> None:
    """Fail if the isolated restore project left resources; never delete them."""
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or os.environ.get('DOCKER_CONTEXT') or
            os.environ.get('DOCKER_HOST') != 'unix:///var/run/docker.sock' or
            os.environ.get('COMPOSE_PROJECT_NAME') != 'survey-production'):
        raise EvidenceError('restore cleanup host binding is invalid')
    label_format = '{{.Label "com.docker.compose.project"}}'
    queries = (
        (['ps', '-aq', '--filter', 'name=^/survey-restore-'], False),
        (['ps', '-a', '--format', label_format], True),
        (['volume', 'ls', '-q', '--filter', 'name=^survey-restore-'], False),
        (['volume', 'ls', '--format', label_format], True),
        (['network', 'ls', '-q', '--filter', 'name=^survey-restore-'], False),
        (['network', 'ls', '--format', label_format], True),
    )
    for command, labels in queries:
        try:
            result = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', *command],
                                    check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EvidenceError('restore cleanup inspection failed') from exc
        lines = result.stdout.splitlines()
        if (any(line.strip().startswith('survey-restore-') for line in lines) if labels else bool(result.stdout.strip())):
            raise EvidenceError('isolated restore project resources remain')


def _fixture_owner(work: Path, target: Path, marker: Path) -> dict[str, str]:
    """Validate the isolated runner identity before constructing an ownership marker."""
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
            os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey' or
            os.environ.get('DOCKER_CONTEXT') or
            os.environ.get('DOCKER_HOST') != 'unix:///var/run/docker.sock' or
            os.environ.get('COMPOSE_PROJECT_NAME') != 'survey-production'):
        raise EvidenceError('fixture runner or Docker binding is invalid')
    run = os.environ.get('GITHUB_RUN_ID', '')
    attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '')
    manifest = os.environ.get('CANDIDATE_MANIFEST_SHA256', '')
    bundle = os.environ.get('CANDIDATE_BUNDLE_SHA256', '')
    ubuntu = os.environ.get('EXPECTED_UBUNTU', '')
    arch = os.environ.get('EXPECTED_ARCHITECTURE', '')
    images = {name: os.environ.get(key, '') for name, key in (
        ('admin', 'ADMIN_IMAGE_DIGEST'), ('platform', 'PLATFORM_IMAGE_DIGEST'),
        ('gateway', 'PUBLISH_GATEWAY_IMAGE_DIGEST'), ('engine', 'ENGINE_IMAGE_DIGEST'))}
    if (re.fullmatch(r'[1-9][0-9]*', run) is None or re.fullmatch(r'[1-9][0-9]*', attempt) is None or
            SHA256.fullmatch(manifest) is None or SHA256.fullmatch(bundle) is None or (ubuntu, arch) not in RUNNERS or
            any(DIGEST.fullmatch(value) is None for value in images.values())):
        raise EvidenceError('fixture candidate or run identity is invalid')
    temp = Path(os.environ.get('RUNNER_TEMP', ''))
    if (not temp.is_absolute() or not work.is_absolute() or work.is_symlink() or not work.is_dir() or
            work.resolve().parent != temp.resolve() or not work.name.startswith('survey-native-acceptance.') or
            target != work / 'primary' or target.is_symlink() or marker != work / 'owner.json'):
        raise EvidenceError('fixture target path is invalid')
    metadata = work.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise EvidenceError('fixture work directory is unsafe')
    return {'repository': 'lsgoodlionel/star-survey', 'run': run, 'attempt': attempt, 'manifest': manifest, 'bundle': bundle,
            'ubuntu': ubuntu, 'architecture': arch, 'target': str(target), 'project': 'survey-production', **images}


def write_fixture_owner(work: Path, marker: Path) -> None:
    owner = _fixture_owner(work, work / 'primary', marker)
    require_clean_native_host()
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(owner, stream, sort_keys=True)
    except OSError as exc:
        raise EvidenceError('fixture owner marker cannot be written') from exc


def verify_fixture_owner(work: Path, target: Path, marker: Path) -> None:
    """Reject accidental run mismatches; this marker alone never authorizes purge.

    The marker must belong to the current effective user. This is not proof of a
    clean host, root ownership, or protection against code running as that user.
    """
    expected = _fixture_owner(work, target, marker)
    try:
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or
                    metadata.st_uid != os.geteuid() or metadata.st_size > 4096):
                raise EvidenceError('fixture owner marker is unsafe')
            if json.loads(stream.read(4097)) != expected:
                raise EvidenceError('fixture owner marker does not match this run')
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError('fixture owner marker cannot be read') from exc


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
    sanitized = PEM_BLOCK.sub(REDACTED, sanitized)
    sanitized = HEADER_CREDENTIAL.sub(lambda match: f"{match.group(1)}: {REDACTED}", sanitized)
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


def exact_keys(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise EvidenceError(f"{label} fields do not match the evidence schema")
    return value


def string(value: Any, label: str, maximum: int, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise EvidenceError(f"{label} must be a non-empty string of at most {maximum} characters")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise EvidenceError(f"{label} has an invalid format")
    return value


def integer(value: Any, label: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvidenceError(f"{label} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise EvidenceError(f"{label} exceeds {maximum}")
    return value


def boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceError(f"{label} must be boolean")
    return value


def validate_doctor(value: Any) -> None:
    report = exact_keys(value, {"schemaVersion", "status", "checks"}, "doctor")
    if report["schemaVersion"] != 1:
        raise EvidenceError("doctor schemaVersion must be 1")
    string(report["status"], "doctor status", 32)
    checks = report["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 64:
        raise EvidenceError("doctor checks must contain between 1 and 64 items")
    for index, item in enumerate(checks):
        check = exact_keys(item, {"id", "status", "summary"}, f"doctor check {index}")
        string(check["id"], f"doctor check {index} id", 64, re.compile(r"[a-z0-9-]+"))
        string(check["status"], f"doctor check {index} status", 32)
        string(check["summary"], f"doctor check {index} summary", 4096)


def validate_services(value: Any) -> None:
    report = exact_keys(value, {"schemaVersion", "services"}, "services")
    if report["schemaVersion"] != 1:
        raise EvidenceError("services schemaVersion must be 1")
    services = report["services"]
    if not isinstance(services, list) or len(services) > 128:
        raise EvidenceError("services must be an array of at most 128 items")
    limits = {"id": 128, "image": 512, "name": 256, "state": 64, "health": 256}
    for index, item in enumerate(services):
        service = exact_keys(item, set(limits), f"service {index}")
        for key, maximum in limits.items():
            string(service[key], f"service {index} {key}", maximum)


def validate_summary(value: Any) -> None:
    summary = exact_keys(value, {
        "schemaVersion", "status", "exitCode", "lastStep", "runner", "candidate",
        "baselineMode", "upgradeVerified", "tls", "preflightDisk", "completedAt",
    }, "summary")
    if summary["schemaVersion"] != 1 or summary["status"] not in {"passed", "failed"}:
        raise EvidenceError("summary schema or status is invalid")
    integer(summary["exitCode"], "summary exitCode", 0, 255)
    string(summary["lastStep"], "summary lastStep", 128, re.compile(r"[a-z0-9-]+"))
    runner = exact_keys(summary["runner"], {"ubuntu", "architecture"}, "summary runner")
    if (runner["ubuntu"], runner["architecture"]) not in RUNNERS:
        raise EvidenceError("summary runner is outside the native matrix")
    candidate = exact_keys(
        summary["candidate"], {"manifestSha256", "bundleSha256", "images"}, "summary candidate",
    )
    string(candidate["manifestSha256"], "candidate manifestSha256", 64, SHA256)
    string(candidate["bundleSha256"], "candidate bundleSha256", 64, SHA256)
    images = exact_keys(candidate["images"], {"admin", "platform", "publish-gateway", "engine"}, "summary images")
    for name, digest in images.items():
        string(digest, f"summary image {name}", 71, DIGEST)
    if summary["baselineMode"] not in {"bootstrap", "predecessor"}:
        raise EvidenceError("summary baselineMode is invalid")
    boolean(summary["upgradeVerified"], "summary upgradeVerified")
    tls = exact_keys(summary["tls"], {"scope", "httpsMarkerVerified", "publicAcmeVerified"}, "summary tls")
    if tls["scope"] != "ci-local-trusted-ca":
        raise EvidenceError("summary TLS scope is invalid")
    boolean(tls["httpsMarkerVerified"], "summary httpsMarkerVerified")
    boolean(tls["publicAcmeVerified"], "summary publicAcmeVerified")
    if (summary['status'] == 'passed' and not tls["httpsMarkerVerified"]) or tls["publicAcmeVerified"]:
        raise EvidenceError("summary TLS assertions are invalid")
    if summary["preflightDisk"] != "separately-tested":
        raise EvidenceError("summary preflight disk assertion is invalid")
    string(summary["completedAt"], "summary completedAt", 64)
    try:
        datetime.fromisoformat(summary["completedAt"])
    except ValueError as exc:
        raise EvidenceError("summary completedAt is invalid") from exc


def validate_preflight(value: Any) -> None:
    report = exact_keys(value, {
        "schemaVersion", "status", "profile", "actualFreeBytes", "requiredFreeBytes",
        "productionRequiredFreeBytes", "assertion",
    }, "preflight")
    if report["schemaVersion"] != 1 or report["status"] != "ok":
        raise EvidenceError("preflight schema or status is invalid")
    if report["profile"] != "github-actions-native-v1":
        raise EvidenceError("preflight profile is invalid")
    actual = integer(report["actualFreeBytes"], "preflight actualFreeBytes", 1)
    required = integer(report["requiredFreeBytes"], "preflight requiredFreeBytes", 1)
    production = integer(report["productionRequiredFreeBytes"], "preflight productionRequiredFreeBytes", 1)
    if actual < required or required != 8 * 1024**3 or production != 20 * 1024**3:
        raise EvidenceError("preflight disk values are invalid")
    if report["assertion"] != "preflight disk separately tested":
        raise EvidenceError("preflight assertion is invalid")


def validate_readiness(value: Any) -> None:
    report = exact_keys(value, {
        "schemaVersion", "candidateVersion", "candidateCommit", "manifestSha256",
        "amd64BundleSha256", "arm64BundleSha256", "adminManifestDigest",
        "platformManifestDigest", "gatewayManifestDigest", "engineManifestDigest", "matrixEvidenceSha256",
        "baselineMode", "nativeMatrixCount", "promotable", "reason",
    }, "release readiness")
    if report["schemaVersion"] != 1:
        raise EvidenceError("release readiness schemaVersion must be 1")
    string(report["candidateVersion"], "release candidateVersion", 64, SEMVER)
    string(report["candidateCommit"], "release candidateCommit", 40, COMMIT)
    for field in ("manifestSha256", "amd64BundleSha256", "arm64BundleSha256"):
        string(report[field], f"release {field}", 64, SHA256)
    for field in ("adminManifestDigest", "platformManifestDigest", "gatewayManifestDigest", "engineManifestDigest"):
        string(report[field], f"release {field}", 71, DIGEST)
    matrix_hashes = exact_keys(
        report["matrixEvidenceSha256"], set(MATRIX_EVIDENCE_KEYS.values()),
        "release matrixEvidenceSha256",
    )
    for runner, digest in matrix_hashes.items():
        string(digest, f"release matrix evidence {runner}", 64, SHA256)
    if report["baselineMode"] not in {"bootstrap", "predecessor"}:
        raise EvidenceError("release readiness baselineMode is invalid")
    integer(report["nativeMatrixCount"], "release nativeMatrixCount", 4, 4)
    boolean(report["promotable"], "release promotable")
    if report["reason"] not in {"upgrade-verified", "bootstrap-not-promotable"}:
        raise EvidenceError("release readiness reason is invalid")
    if report["promotable"] != (report["reason"] == "upgrade-verified"):
        raise EvidenceError("release readiness promotion fields disagree")


def validate_engine_health(value: Any) -> None:
    report = exact_keys(value, {'schemaVersion', 'status', 'probeExitCodes', 'probeCategories'}, 'engine health')
    if report['schemaVersion'] != 1 or report['status'] not in {'healthy', 'unhealthy', 'starting', 'unknown'}:
        raise EvidenceError('engine health status is invalid')
    if not isinstance(report['probeExitCodes'], list) or len(report['probeExitCodes']) > 5:
        raise EvidenceError('engine health probe list is invalid')
    for code in report['probeExitCodes']:
        integer(code, 'engine health probe exit code', -1, 255)
    categories = report['probeCategories']
    if not isinstance(categories, list) or len(categories) != len(report['probeExitCodes']) or any(
        category not in {'timeout', 'php-error', 'unknown'} for category in categories
    ):
        raise EvidenceError('engine health probe categories are invalid')


def validate_gateway_diagnostic(value: Any) -> None:
    report = exact_keys(value, {'schemaVersion', 'observedCategories'}, 'gateway diagnostic')
    allowed = {'connection-refused', 'connection-timeout', 'tls-failure', 'unknown'}
    categories = report['observedCategories']
    if report['schemaVersion'] != 1 or not isinstance(categories, list) or not categories or len(categories) > len(allowed) or any(c not in allowed for c in categories):
        raise EvidenceError('gateway diagnostic categories are invalid')


VALIDATORS = {
    "doctor": validate_doctor,
    "services": validate_services,
    "summary": validate_summary,
    "preflight": validate_preflight,
    "readiness": validate_readiness,
    "engine-health": validate_engine_health,
    "gateway-diagnostic": validate_gateway_diagnostic,
}


def validate_upgrade_data(value: Any) -> None:
    flags = {'versionSwitchVerified', 'responseBeforeUpgradeVerified', 'responseAfterUpgradeVerified',
             'exportAnswerVerified', 'cleanupCompleted'}
    report = exact_keys(value, {'schemaVersion', 'status', 'baselineVersion', 'candidateVersion',
                               'baselineManifestSha256', 'candidateManifestSha256'} | flags, 'upgrade data')
    integer(report['schemaVersion'], 'upgrade data schemaVersion', 1, 1)
    if report['status'] != 'passed':
        raise EvidenceError('upgrade data evidence is not passed')
    for field in ('baselineVersion', 'candidateVersion'):
        string(report[field], field, 128, SEMVER)
    if report['baselineVersion'] == report['candidateVersion']:
        raise EvidenceError('upgrade data must cross distinct versions')
    for field in ('baselineManifestSha256', 'candidateManifestSha256'):
        string(report[field], field, 64, SHA256)
    if any(report[field] is not True for field in flags):
        raise EvidenceError('upgrade data assertions must all pass')


VALIDATORS['upgrade-data'] = validate_upgrade_data


def validate_evidence(kind: str, value: Any, secret_values: Iterable[str] = ()) -> None:
    validator = VALIDATORS.get(kind)
    if validator is None:
        raise EvidenceError(f"unknown evidence kind: {kind}")
    validator(value)
    variants = secret_variants(secret_values)

    def inspect(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if sensitive_key(key):
                    raise EvidenceError("sensitive field names are forbidden in evidence")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
        elif isinstance(item, str):
            if any(secret in item for secret in variants) or sanitize_string(item) != item:
                raise EvidenceError("credential-like content is forbidden in evidence strings")

    inspect(value)


def write_evidence(path: Path, kind: str, value: Any, secret_values: Iterable[str] = ()) -> None:
    validate_evidence(kind, value, secret_values)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise EvidenceError("evidence exceeds the maximum size")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def write_doctor_evidence(report: dict[str, Any], output: Path, secret_values: Iterable[str] = ()) -> None:
    if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
        raise EvidenceError("doctor output is not structured JSON")
    checks = []
    for item in report["checks"]:
        if not isinstance(item, dict):
            raise EvidenceError("doctor check is not an object")
        summary = item.get("summary", "")
        checks.append({
            "id": item.get("id"),
            "status": item.get("status"),
            "summary": sanitize_string(summary, secret_values) if isinstance(summary, str) else summary,
        })
    write_evidence(output, "doctor", {
        "schemaVersion": 1,
        "status": report.get("status"),
        "checks": checks,
    }, secret_values)


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
        services.append({
            key: sanitize_string(item.get(key, ""), secret_values)
            if isinstance(item.get(key, ""), str) else item.get(key)
            for key in ("id", "image", "name", "state", "health")
        })
    write_evidence(output, "services", {"schemaVersion": 1, "services": services}, secret_values)


def tls_marker_from_doctor(report: Any) -> bool:
    return isinstance(report, dict) and isinstance(report.get('checks'), list) and any(
        isinstance(item, dict) and item.get('id') == 'tls' and item.get('status') == 'ok'
        for item in report['checks'])


def write_gateway_diagnostic_evidence(log: str, output: Path) -> None:
    # Record only fixed classes observed anywhere in this disposable gateway's
    # output. These are diagnostic observations, not proof of root cause.
    categories = set()
    lowered = log.lower()
    for marker, category in [('connection refused', 'connection-refused'),
                             ('timed out', 'connection-timeout'),
                             ('certificate verify failed', 'tls-failure')]:
        if marker in lowered:
            categories.add(category)
    write_evidence(output, 'gateway-diagnostic', {'schemaVersion': 1,
                   'observedCategories': sorted(categories or {'unknown'})})


def write_engine_health_evidence(health: Any, output: Path) -> None:
    if not isinstance(health, dict):
        raise EvidenceError('engine health is not an object')
    status = health.get('Status')
    if status not in {'healthy', 'unhealthy', 'starting'}:
        status = 'unknown'
    logs = health.get('Log', [])
    if not isinstance(logs, list):
        raise EvidenceError('engine health log is invalid')
    codes = [entry.get('ExitCode') for entry in logs[-5:] if isinstance(entry, dict)]
    categories = []
    for entry in logs[-5:]:
        if not isinstance(entry, dict):
            continue
        output_text = entry.get('Output', '')
        if isinstance(output_text, str) and 'Health check exceeded timeout' in output_text:
            categories.append('timeout')
        elif isinstance(output_text, str) and re.search(r'CException|CDbException|Fatal error|Parse error', output_text):
            categories.append('php-error')
        else:
            categories.append('unknown')
    write_evidence(output, 'engine-health', {'schemaVersion': 1, 'status': status,
                   'probeExitCodes': codes, 'probeCategories': categories})


def write_summary_evidence(output: Path, status: str, exit_code: int, last_step: str, upgrade_verified: bool, doctor: Any = None,
                           upgrade_data: Any = None) -> None:
    if upgrade_verified:
        validate_evidence('upgrade-data', upgrade_data)
        if os.environ['BASELINE_MODE'] != 'predecessor' or upgrade_data['candidateManifestSha256'] != os.environ['CANDIDATE_MANIFEST_SHA256']:
            raise EvidenceError('upgrade data does not match the native candidate')
    write_evidence(output, "summary", {
        "schemaVersion": 1,
        "status": status,
        "exitCode": exit_code,
        "lastStep": last_step,
        "runner": {"ubuntu": os.environ["EXPECTED_UBUNTU"], "architecture": os.environ["EXPECTED_ARCHITECTURE"]},
        "candidate": {
            "manifestSha256": os.environ["CANDIDATE_MANIFEST_SHA256"],
            "bundleSha256": os.environ["CANDIDATE_BUNDLE_SHA256"],
            "images": {
                "admin": os.environ["ADMIN_IMAGE_DIGEST"],
                "platform": os.environ["PLATFORM_IMAGE_DIGEST"],
                "publish-gateway": os.environ["PUBLISH_GATEWAY_IMAGE_DIGEST"],
                "engine": os.environ["ENGINE_IMAGE_DIGEST"],
            },
        },
        "baselineMode": os.environ["BASELINE_MODE"],
        "upgradeVerified": upgrade_verified,
        "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": tls_marker_from_doctor(doctor), "publicAcmeVerified": False},
        "preflightDisk": "separately-tested",
        "completedAt": datetime.now(timezone.utc).isoformat(),
    })


def write_preflight_evidence(output: Path, actual: int, required: int) -> None:
    write_evidence(output, "preflight", {
        "schemaVersion": 1,
        "status": "ok",
        "profile": "github-actions-native-v1",
        "actualFreeBytes": actual,
        "requiredFreeBytes": required,
        "productionRequiredFreeBytes": 20 * 1024**3,
        "assertion": "preflight disk separately tested",
    })


def build_release_readiness(
    identity: dict[str, Any], summaries: list[dict[str, Any]], evidence_hashes: dict[str, str],
) -> dict[str, Any]:
    version = string(identity.get("version"), "identity version", 64, SEMVER)
    commit = string(identity.get("commit"), "identity commit", 40, COMMIT)
    baseline = identity.get("baseline")
    if not isinstance(baseline, dict) or baseline.get("mode") not in {"bootstrap", "predecessor"}:
        raise EvidenceError("identity baseline mode is invalid")
    mode = baseline["mode"]
    if len(summaries) != 4:
        raise EvidenceError("release readiness requires exactly four native summaries")
    for summary in summaries:
        validate_evidence("summary", summary)
    runners = {(item["runner"]["ubuntu"], item["runner"]["architecture"]) for item in summaries}
    if runners != RUNNERS:
        raise EvidenceError("release readiness summaries do not cover the exact native matrix")
    if set(evidence_hashes) != set(MATRIX_EVIDENCE_KEYS.values()):
        raise EvidenceError("release readiness evidence hashes do not cover the exact native matrix")
    for runner, digest in evidence_hashes.items():
        string(digest, f"native evidence hash {runner}", 64, SHA256)
    if any(item["status"] != "passed" or item["exitCode"] != 0 or item["lastStep"] != "uninstall" for item in summaries):
        raise EvidenceError("native acceptance did not complete successfully")
    if any(item["baselineMode"] != mode for item in summaries):
        raise EvidenceError("native summaries disagree with the baseline identity")
    manifest_sha = string(identity.get("manifestSha256"), "identity manifestSha256", 64, SHA256)
    bundles = identity.get("bundles")
    images = identity.get("images")
    if not isinstance(bundles, dict) or set(bundles) != {"amd64", "arm64"} or not isinstance(images, dict):
        raise EvidenceError("candidate identity does not contain the complete native release identity")
    exact_keys(images, {"admin", "platform", "publish-gateway", "engine"}, "identity images")
    expected_images = {}
    for name in ("admin", "platform", "publish-gateway", "engine"):
        image = images.get(name)
        if not isinstance(image, dict):
            raise EvidenceError(f"candidate identity image is missing: {name}")
        expected_images[name] = string(image.get("digest"), f"identity image {name}", 71, DIGEST)
    for summary in summaries:
        architecture = summary["runner"]["architecture"]
        bundle = bundles[architecture]
        if not isinstance(bundle, dict):
            raise EvidenceError(f"candidate identity bundle is invalid: {architecture}")
        bundle_sha = string(bundle.get("sha256"), f"identity bundle {architecture}", 64, SHA256)
        candidate = summary["candidate"]
        if candidate["manifestSha256"] != manifest_sha or candidate["bundleSha256"] != bundle_sha:
            raise EvidenceError("native summary checksum does not match the candidate identity")
        if candidate["images"] != expected_images:
            raise EvidenceError("native summary image digests do not match the candidate identity")
    stable = "-rc." not in version
    if stable and mode != "predecessor":
        raise EvidenceError("stable releases require a predecessor baseline")
    if mode == "bootstrap":
        if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-rc\.1", version) is None:
            raise EvidenceError("bootstrap is only valid for the first prerelease RC")
        if any(item["upgradeVerified"] for item in summaries):
            raise EvidenceError("bootstrap summaries cannot claim an upgrade")
        promotable, reason = False, "bootstrap-not-promotable"
    else:
        if any(not item["upgradeVerified"] for item in summaries):
            raise EvidenceError("predecessor acceptance must verify upgrade on every native runner")
        promotable, reason = True, "upgrade-verified"
    return {
        "schemaVersion": 1,
        "candidateVersion": version,
        "candidateCommit": commit,
        "manifestSha256": manifest_sha,
        "amd64BundleSha256": string(bundles["amd64"].get("sha256"), "identity bundle amd64", 64, SHA256),
        "arm64BundleSha256": string(bundles["arm64"].get("sha256"), "identity bundle arm64", 64, SHA256),
        "adminManifestDigest": expected_images["admin"],
        "platformManifestDigest": expected_images["platform"],
        "gatewayManifestDigest": expected_images["publish-gateway"],
        "engineManifestDigest": expected_images["engine"],
        "matrixEvidenceSha256": dict(sorted(evidence_hashes.items())),
        "baselineMode": mode,
        "nativeMatrixCount": 4,
        "promotable": promotable,
        "reason": reason,
    }


def write_release_readiness(
    identity: dict[str, Any], summaries: list[dict[str, Any]], evidence_hashes: dict[str, str], output: Path,
) -> None:
    write_evidence(output, "readiness", build_release_readiness(identity, summaries, evidence_hashes))


def sha256_file(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"{label} is not an unlinked regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_native_summaries(evidence_directory: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    paths = sorted(evidence_directory.glob("*/summary.json"))
    summaries: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for path in paths:
        summary = load_json(path)
        validate_evidence("summary", summary)
        runner = summary["runner"]
        key = MATRIX_EVIDENCE_KEYS[(runner["ubuntu"], runner["architecture"])]
        if key in hashes:
            raise EvidenceError(f"duplicate native evidence runner: {key}")
        summaries.append(summary)
        hashes[key] = sha256_file(path, f"native evidence {key}")
    return summaries, hashes


def verify_candidate_assets(identity: dict[str, Any], candidate_directory: Path) -> None:
    version = string(identity.get("version"), "identity version", 64, SEMVER)
    commit = string(identity.get("commit"), "identity commit", 40, COMMIT)
    manifest_path = candidate_directory / "release.json"
    manifest = load_json(manifest_path)
    manifest_sha = sha256_file(manifest_path, "candidate manifest")
    if manifest_sha != string(identity.get("manifestSha256"), "identity manifestSha256", 64, SHA256):
        raise EvidenceError("downloaded candidate manifest checksum does not match identity")
    if manifest.get("version") != version or manifest.get("commit") != commit:
        raise EvidenceError("downloaded candidate version or commit does not match identity")
    bundles = identity.get("bundles")
    if not isinstance(bundles, dict) or set(bundles) != {"amd64", "arm64"}:
        raise EvidenceError("candidate identity bundles are incomplete")
    if manifest.get("assets", {}).get("bundles") != bundles:
        raise EvidenceError("downloaded candidate bundle manifest does not match identity")
    for architecture, bundle in bundles.items():
        record = exact_keys(bundle, {"name", "sha256"}, f"identity bundle {architecture}")
        name = string(record["name"], f"identity bundle {architecture} name", 256, re.compile(r"[A-Za-z0-9._-]+"))
        expected_sha = string(record["sha256"], f"identity bundle {architecture} sha256", 64, SHA256)
        if sha256_file(candidate_directory / name, f"candidate bundle {architecture}") != expected_sha:
            raise EvidenceError(f"downloaded candidate bundle checksum mismatch: {architecture}")
    images = identity.get("images")
    if not isinstance(images, dict) or set(images) != {"admin", "platform", "publish-gateway", "engine"}:
        raise EvidenceError("candidate identity images are incomplete")
    manifest_keys = {
        "admin": "ADMIN_IMAGE", "platform": "PLATFORM_IMAGE", "publish-gateway": "PUBLISH_GATEWAY_IMAGE",
        "engine": "ENGINE_IMAGE",
    }
    for name, manifest_key in manifest_keys.items():
        image = exact_keys(images[name], {"reference", "digest"}, f"identity image {name}")
        digest = string(image["digest"], f"identity image {name} digest", 71, DIGEST)
        reference = string(image["reference"], f"identity image {name} reference", 512)
        if not reference.endswith("@" + digest) or manifest.get("images", {}).get(manifest_key) != reference:
            raise EvidenceError(f"downloaded candidate image manifest mismatch: {name}")


def write_release_readiness_from_assets(
    identity_path: Path, candidate_directory: Path, evidence_directory: Path, output: Path,
) -> None:
    identity = load_json(identity_path)
    verify_candidate_assets(identity, candidate_directory)
    summaries, hashes = load_native_summaries(evidence_directory)
    verify_upgrade_data_assets(identity, evidence_directory)
    write_release_readiness(identity, summaries, hashes, output)


def verify_release_readiness_assets(
    readiness_path: Path, identity_path: Path, candidate_directory: Path, evidence_directory: Path,
) -> None:
    readiness = load_json(readiness_path)
    validate_evidence("readiness", readiness)
    identity = load_json(identity_path)
    verify_candidate_assets(identity, candidate_directory)
    summaries, hashes = load_native_summaries(evidence_directory)
    verify_upgrade_data_assets(identity, evidence_directory)
    expected = build_release_readiness(identity, summaries, hashes)
    if readiness != expected:
        raise EvidenceError("release readiness does not match independently recomputed downloaded assets")


def verify_upgrade_data_assets(identity: dict[str, Any], evidence_directory: Path) -> None:
    for path in evidence_directory.glob('*/summary.json'):
        proof_path = path.parent / 'upgrade-data.json'
        if identity['baseline']['mode'] == 'bootstrap':
            if proof_path.exists():
                raise EvidenceError('bootstrap cannot include an upgrade data assertion')
            continue
        report = load_json(proof_path)
        validate_evidence('upgrade-data', report)
        expected = {'candidateVersion': identity['version'], 'candidateManifestSha256': identity['manifestSha256'],
                    'baselineVersion': identity['baseline'].get('version'),
                    'baselineManifestSha256': identity['baseline'].get('manifestSha256')}
        if any(report[field] != value for field, value in expected.items()):
            raise EvidenceError('upgrade data identity differs from the candidate or predecessor')


def evidence_kind(path: Path) -> str:
    if DOCTOR_FILE.fullmatch(path.name):
        return "doctor"
    kinds = {"summary.json": "summary", "preflight.json": "preflight", "services.json": "services", "engine-health.json": "engine-health", "gateway-diagnostic.json": "gateway-diagnostic", 'upgrade-data.json': 'upgrade-data'}
    try:
        return kinds[path.name]
    except KeyError as exc:
        raise EvidenceError(f"evidence filename is not allowlisted: {path.name}") from exc


def load_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.suffix != ".json":
        raise EvidenceError(f"unexpected evidence artifact: {path.name}")
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise EvidenceError(f"evidence exceeds the maximum size: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"evidence is not valid JSON: {path.name}") from exc


def scan_directory(directory: Path, secret_values: Iterable[str] = ()) -> None:
    for path in directory.iterdir():
        value = load_json(path)
        validate_evidence(evidence_kind(path), value, secret_values)


def verify_continuity_export(payload, original):
    """Read the actual CsvExportWriter ZIP schema; never extract files or emit answer data."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        tables = {}
        for name in ('responses.csv', 'fields.csv'):
            if names.count(name) != 1:
                raise ValueError('missing or duplicate export table')
            info = archive.getinfo(name)
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError('export table exceeds probe bound')
            tables[name] = list(csv.reader(io.StringIO(archive.read(name).decode('utf-8-sig')), strict=True))
    responses, fields = tables['responses.csv'], tables['fields.csv']
    fixed = ['version', 'sid', 'responseid', 'generation', 'state', 'startedat', 'completedat', 'answersstatus']
    dictionary = ['version', 'sid', 'column', 'fieldname', 'questioncode', 'type', 'aid', 'scale', 'label', 'sensitive', 'masking', 'options']
    if (len(responses) < 3 or responses[0][:8] != fixed or
            len(set(responses[0])) != len(responses[0]) or
            any(len(row) != len(responses[0]) for row in responses[1:]) or
            not fields or fields[0] != dictionary or any(len(row) != len(dictionary) for row in fields[1:])):
        raise ValueError('unexpected export schema')
    identity = [str(original[key]) for key in ('version', 'engineSid', 'responseId', 'generation')]
    answer_fields = [key for key, answer in original['answers'].items() if answer == 'production acceptance']
    if len(answer_fields) != 1:
        raise ValueError('ambiguous original probe answer')
    mappings = [row for row in fields[1:] if row[0:2] == identity[0:2] and row[3] == answer_fields[0]]
    if (len(mappings) != 1 or mappings[0][4:8] != ['PROBE', 'S', '', '0'] or
            mappings[0][2] not in responses[0][8:]):
        raise ValueError('missing probe field mapping')
    answer_index = responses[0].index(mappings[0][2])
    # ExportLayout has TWO header rows; neither can count as a response.
    matches = [row for row in responses[2:] if row[:4] == identity]
    if (len(matches) != 1 or matches[0][4] != original['state'] or
            matches[0][7] != 'available' or matches[0][answer_index] != original['answers'][answer_fields[0]]):
        raise ValueError('original exported response is missing or ambiguous')


def make_continuity_probe(base_probe, error_type):
    class ContinuityProbe(base_probe):
        def __init__(self, *args, upgrade_action, upgraded_compose, **kwargs):
            super().__init__(*args, **kwargs)
            self.upgrade_action = upgrade_action
            self.upgraded_compose = upgraded_compose
            self.before_items = self.response_path = self.fixture_identity = None
            self.after_token = None
            self.upgrade_completed = self.response_verified = self.export_verified = False
            self.continuity_phase = 'prepare'
            self.upgrade_failure_category, self.upgrade_exit_code = 'none', None

        def _token(self, config):
            if self.fixture_identity is None:
                self.fixture_identity = dict(config)
            elif config != self.fixture_identity:
                raise error_type('continuity probe identity changed')
            return super()._token(config)

        def _api(self, token, method, path, body=None, raw=False):
            if method == 'POST' and self.response_path and path == self.response_path.split('/responses?', 1)[0] + '/exports':
                if self.upgrade_completed or not self.before_items:
                    raise error_type('continuity upgrade must execute exactly once after the original answer')
                self.continuity_phase = 'upgrade'
                try:
                    self.upgrade_action()
                except Exception as exc:
                    self.upgrade_failure_category = getattr(exc, 'upgrade_category', 'unknown')
                    self.upgrade_exit_code = getattr(exc, 'upgrade_exit_code', None)
                    raise
                self.upgrade_completed = True
                self.compose = self.upgraded_compose()
                self.after_token = self._token(self._configuration())
                self.continuity_phase = 'response-read'
                status, responses = super()._api(self.after_token, 'GET', self.response_path)
                if status != 200 or not isinstance(responses, dict) or responses.get('items') != self.before_items:
                    raise error_type('original response changed or disappeared across upgrade')
                self.response_verified = True
            status, value = super()._api(self.after_token or token, method, path, body, raw)
            if not self.upgrade_completed and method == 'GET' and '/responses?' in path and status == 200:
                if isinstance(value, dict) and isinstance(value.get('items'), list) and value['items']:
                    self.continuity_phase = 'pre-upgrade-read'
                    if len(value['items']) != 1:
                        raise error_type('continuity fixture must contain exactly one response')
                    row = value['items'][0]
                    if not isinstance(row, dict) or row.get('answersStatus') != 'available' or not isinstance(row.get('answers'), dict) or 'production acceptance' not in row['answers'].values():
                        raise error_type('original engine answer is unavailable before upgrade')
                    self.before_items = json.loads(json.dumps(value['items']))
                    self.response_path = path
            if self.upgrade_completed and raw and method == 'GET' and path.endswith('/download'):
                self.continuity_phase = 'export-read'
                if status != 200 or not isinstance(value, bytes):
                    raise error_type('upgraded export is unavailable')
                try:
                    verify_continuity_export(value, self.before_items[0])
                except (ValueError, UnicodeDecodeError, csv.Error, zipfile.BadZipFile, KeyError, OSError) as exc:
                    raise error_type('original response answer is missing or ambiguous in the upgraded export') from exc
                self.export_verified = True
            return status, value

        def run_and_verify(self):
            try:
                self.run()  # Existing product journey owns engine close and project archive.
            except Exception as exc:
                if getattr(exc, 'stage', None) == 'cleanup': self.continuity_phase = 'cleanup'
                raise
            if not all((self.before_items, self.upgrade_completed, self.response_verified, self.export_verified)):
                raise error_type('continuity journey did not complete')
            self.continuity_phase = 'complete'
            return {'versionSwitchVerified': True, 'responseBeforeUpgradeVerified': True,
                    'responseAfterUpgradeVerified': True, 'exportAnswerVerified': True, 'cleanupCompleted': True}
    return ContinuityProbe


def perform_upgrade(ctl, target, manifest, manifest_sha, version, manager, error_type):
    try:
        subprocess.run([str(ctl), '--target', str(target), 'upgrade', '--manifest', str(manifest),
                        '--manifest-sha256', manifest_sha], check=True, capture_output=True, text=True, timeout=600)
        if manager._read_state(require_current=True)['version'] != version:
            raise error_type('upgrade did not activate the expected version')
    except Exception as exc:
        failure = error_type('continuity upgrade or version verification failed')
        failure.upgrade_category = ('cli-exit' if isinstance(exc, subprocess.CalledProcessError) else
                                    'cli-timeout' if isinstance(exc, subprocess.TimeoutExpired) else
                                    'cli-launch' if isinstance(exc, OSError) else 'version-check')
        failure.upgrade_exit_code = exc.returncode if isinstance(exc, subprocess.CalledProcessError) else None
        raise failure from None


def record_continuity(probe, baseline, candidate, baseline_sha, candidate_sha, output, writer):
    flags = probe.run_and_verify()
    # Persist only after the original product probe has finished all cleanup.
    probe.continuity_phase = 'evidence-write'
    writer(output, 'upgrade-data', {'schemaVersion': 1, 'status': 'passed',
           'baselineVersion': baseline, 'candidateVersion': candidate,
           'baselineManifestSha256': baseline_sha, 'candidateManifestSha256': candidate_sha, **flags})
    probe.continuity_phase = 'complete'


def run_upgrade_data_probe(args):
    if any(os.environ.get(key) != value for key, value in {'NATIVE_ACCEPTANCE_REAL': '1', 'GITHUB_ACTIONS': 'true',
                                                         'SURVEY_NATIVE_ACCEPTANCE': '1'}.items()) or not args.host.endswith('.test'):
        raise EvidenceError('real isolated GitHub native acceptance context required')
    baseline_path, candidate_path = Path(args.baseline_manifest), Path(args.candidate_manifest)
    for path, expected in ((baseline_path, args.baseline_sha), (candidate_path, args.candidate_sha)):
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise EvidenceError('continuity manifest identity mismatch')
    baseline, candidate = json.loads(baseline_path.read_text()), json.loads(candidate_path.read_text())
    sys.path.insert(0, args.runtime)
    from doctor import ProductProbe, DoctorRuntimeError, CommandRunner
    from surveyctl import SurveyManager
    manager = SurveyManager(args.target)
    if manager._read_state(require_current=True)['version'] != baseline['version']:
        raise EvidenceError('installed version differs from the verified predecessor')
    Probe = make_continuity_probe(ProductProbe, DoctorRuntimeError)
    probe = Probe(args.target, args.host, manager._compose(baseline['version']), CommandRunner(),
                  upgrade_action=lambda: perform_upgrade(args.ctl, args.target, candidate_path, args.candidate_sha,
                                                         candidate['version'], manager, DoctorRuntimeError),
                  upgraded_compose=lambda: manager._compose(candidate['version']))
    try:
        record_continuity(probe, baseline['version'], candidate['version'], args.baseline_sha, args.candidate_sha,
                          Path(args.output), write_evidence)
    except Exception as exc:
        status = getattr(exc, 'status', None)
        status = status if type(status) is int and 100 <= status <= 599 else None
        print(f'upgrade data continuity failed phase={probe.continuity_phase} http={status} '
              f'upgrade={probe.upgrade_failure_category} exit={probe.upgrade_exit_code}', file=sys.stderr)
        return 4
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--secrets-dir", type=Path)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser('clean-host')
    sub.add_parser('clean-restore')
    for action in ('fixture-owner-create', 'fixture-owner-verify'):
        owner = sub.add_parser(action)
        owner.add_argument('--work', type=Path, required=True)
        owner.add_argument('--marker', type=Path, required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--input", type=Path, required=True)
    doctor.add_argument("--output", type=Path, required=True)
    services = sub.add_parser("services")
    services.add_argument("--input", type=Path, required=True)
    services.add_argument("--output", type=Path, required=True)
    health = sub.add_parser('engine-health')
    health.add_argument('--input', type=Path, required=True)
    health.add_argument('--output', type=Path, required=True)
    gateway = sub.add_parser('gateway-diagnostic')
    gateway.add_argument('--output', type=Path, required=True)
    upgrade = sub.add_parser('upgrade-data-probe')
    for name in ('runtime', 'ctl', 'target', 'host', 'baseline-manifest', 'baseline-sha',
                 'candidate-manifest', 'candidate-sha', 'output'):
        upgrade.add_argument('--' + name, required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--output", type=Path, required=True)
    summary.add_argument("--status", choices=("passed", "failed"), required=True)
    summary.add_argument("--exit-code", type=int, required=True)
    summary.add_argument("--last-step", required=True)
    summary.add_argument("--upgrade-verified", choices=("true", "false"), required=True)
    summary.add_argument('--doctor-evidence', type=Path)
    summary.add_argument('--upgrade-data-evidence', type=Path)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output", type=Path, required=True)
    preflight.add_argument("--actual-free-bytes", type=int, required=True)
    preflight.add_argument("--required-free-bytes", type=int, required=True)
    readiness = sub.add_parser("readiness")
    readiness.add_argument("--identity", type=Path, required=True)
    readiness.add_argument("--candidate-directory", type=Path, required=True)
    readiness.add_argument("--evidence-directory", type=Path, required=True)
    readiness.add_argument("--output", type=Path, required=True)
    verify_readiness = sub.add_parser("verify-readiness")
    verify_readiness.add_argument("--readiness", type=Path, required=True)
    verify_readiness.add_argument("--identity", type=Path, required=True)
    verify_readiness.add_argument("--candidate-directory", type=Path, required=True)
    verify_readiness.add_argument("--evidence-directory", type=Path, required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--directory", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    secrets = load_secret_values(args.secrets_dir)
    try:
        if args.command == 'clean-host':
            require_clean_native_host()
        elif args.command == 'clean-restore':
            require_no_restore_resources()
        elif args.command == 'fixture-owner-create':
            write_fixture_owner(args.work, args.marker)
        elif args.command == 'fixture-owner-verify':
            verify_fixture_owner(args.work, args.work / 'primary', args.marker)
        elif args.command == "doctor":
            write_doctor_evidence(load_json(args.input), args.output, secrets)
        elif args.command == "services":
            write_service_evidence(args.input.read_text(encoding="utf-8"), args.output, secrets)
        elif args.command == 'engine-health':
            write_engine_health_evidence(load_json(args.input), args.output)
        elif args.command == 'gateway-diagnostic':
            write_gateway_diagnostic_evidence(sys.stdin.read(1024 * 1024), args.output)
        elif args.command == 'upgrade-data-probe':
            return run_upgrade_data_probe(args)
        elif args.command == "summary":
            write_summary_evidence(
                args.output, args.status, args.exit_code, args.last_step, args.upgrade_verified == "true",
                load_json(args.doctor_evidence) if args.doctor_evidence and args.doctor_evidence.exists() else None,
                load_json(args.upgrade_data_evidence) if args.upgrade_data_evidence and args.upgrade_data_evidence.exists() else None,
            )
        elif args.command == "preflight":
            write_preflight_evidence(args.output, args.actual_free_bytes, args.required_free_bytes)
        elif args.command == "readiness":
            write_release_readiness_from_assets(
                args.identity, args.candidate_directory, args.evidence_directory, args.output,
            )
        elif args.command == "verify-readiness":
            verify_release_readiness_assets(
                args.readiness, args.identity, args.candidate_directory, args.evidence_directory,
            )
        else:
            scan_directory(args.directory, secrets)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, EvidenceError) as exc:
        print(f"native acceptance evidence failed: {exc}", file=__import__("sys").stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
