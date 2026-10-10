#!/usr/bin/env python3
"""Generate and validate strict native release acceptance evidence."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
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
    images = exact_keys(candidate["images"], {"admin", "platform", "publish-gateway"}, "summary images")
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
    if not tls["httpsMarkerVerified"] or tls["publicAcmeVerified"]:
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
        "platformManifestDigest", "gatewayManifestDigest", "matrixEvidenceSha256",
        "baselineMode", "nativeMatrixCount", "promotable", "reason",
    }, "release readiness")
    if report["schemaVersion"] != 1:
        raise EvidenceError("release readiness schemaVersion must be 1")
    string(report["candidateVersion"], "release candidateVersion", 64, SEMVER)
    string(report["candidateCommit"], "release candidateCommit", 40, COMMIT)
    for field in ("manifestSha256", "amd64BundleSha256", "arm64BundleSha256"):
        string(report[field], f"release {field}", 64, SHA256)
    for field in ("adminManifestDigest", "platformManifestDigest", "gatewayManifestDigest"):
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


VALIDATORS = {
    "doctor": validate_doctor,
    "services": validate_services,
    "summary": validate_summary,
    "preflight": validate_preflight,
    "readiness": validate_readiness,
}


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


def write_summary_evidence(output: Path, status: str, exit_code: int, last_step: str, upgrade_verified: bool) -> None:
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
            },
        },
        "baselineMode": os.environ["BASELINE_MODE"],
        "upgradeVerified": upgrade_verified,
        "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": True, "publicAcmeVerified": False},
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
    expected_images = {}
    for name in ("admin", "platform", "publish-gateway"):
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
    if not isinstance(images, dict) or set(images) != {"admin", "platform", "publish-gateway"}:
        raise EvidenceError("candidate identity images are incomplete")
    manifest_keys = {
        "admin": "ADMIN_IMAGE", "platform": "PLATFORM_IMAGE", "publish-gateway": "PUBLISH_GATEWAY_IMAGE",
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
    write_release_readiness(identity, summaries, hashes, output)


def verify_release_readiness_assets(
    readiness_path: Path, identity_path: Path, candidate_directory: Path, evidence_directory: Path,
) -> None:
    readiness = load_json(readiness_path)
    validate_evidence("readiness", readiness)
    identity = load_json(identity_path)
    verify_candidate_assets(identity, candidate_directory)
    summaries, hashes = load_native_summaries(evidence_directory)
    expected = build_release_readiness(identity, summaries, hashes)
    if readiness != expected:
        raise EvidenceError("release readiness does not match independently recomputed downloaded assets")


def evidence_kind(path: Path) -> str:
    if DOCTOR_FILE.fullmatch(path.name):
        return "doctor"
    kinds = {"summary.json": "summary", "preflight.json": "preflight", "services.json": "services"}
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
    summary = sub.add_parser("summary")
    summary.add_argument("--output", type=Path, required=True)
    summary.add_argument("--status", choices=("passed", "failed"), required=True)
    summary.add_argument("--exit-code", type=int, required=True)
    summary.add_argument("--last-step", required=True)
    summary.add_argument("--upgrade-verified", choices=("true", "false"), required=True)
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
        if args.command == "doctor":
            write_doctor_evidence(load_json(args.input), args.output, secrets)
        elif args.command == "services":
            write_service_evidence(args.input.read_text(encoding="utf-8"), args.output, secrets)
        elif args.command == "summary":
            write_summary_evidence(
                args.output, args.status, args.exit_code, args.last_step, args.upgrade_verified == "true",
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
