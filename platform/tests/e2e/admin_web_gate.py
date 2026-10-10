#!/usr/bin/env python3

"""Prepare and verify the real-stack admin-web browser gate.

Secrets are accepted only through the environment or private files. This
program never prints JWTs, engine event secrets, or complete HTTP payloads.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import unquote_to_bytes, urlsplit
from urllib.request import Request, urlopen


TOKEN_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 300
OPERATOR_ACTOR = "admin-web-e2e-operator"
OWNER_ACTOR = "admin-web-e2e-owner"
EDITOR_ACTOR = "admin-web-e2e-editor"
REVIEWER_ACTOR = "admin-web-e2e-reviewer"
DATA_ACTOR = "admin-web-e2e-data"
TEST_BROWSER_ACTORS = {OWNER_ACTOR, EDITOR_ACTOR, REVIEWER_ACTOR, DATA_ACTOR}
OPERATOR_ROLE = "platform_operator"
RESULT_FIELDS = {
    "rootProjectId",
    "folderId",
    "surveyId",
    "tenantId",
    "version",
    "network",
}
SENSITIVE_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".zip", ".webm"}
SANITIZED_TRACE_FIELDS = {
    "schemaVersion",
    "kind",
    "project",
    "testId",
    "status",
    "durationMs",
    "lastPath",
    "network",
}
SANITIZED_PROJECT_TESTS = {
    "chromium-desktop": "desktop-authoring",
    "chromium-mobile": "mobile-responsive-editor",
}
SANITIZED_STATUSES = {"failed", "timedOut", "interrupted"}
SANITIZED_NETWORK_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
MAX_SANITIZED_NETWORK_EVENTS = 25
MAX_SANITIZED_NETWORK_URL_LENGTH = 768
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
UUID_PATH = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ADMIN_WEB_PATH = re.compile(
    r"^(?:/|/dashboard|/workspace|/login|/auth/callback|/dev/token|"
    r"/surveys/" + UUID_PATH + r"/(?:edit|import|preview|publish|responses)|"
    r"/surveys/" + UUID_PATH + r"/versions/[1-9][0-9]*)$"
)
DASHBOARD_SCREENSHOT_WIDTHS = (819, 820, 1179, 1180)
DASHBOARD_SCREENSHOT_MANIFEST_FIELDS = {
    "schemaVersion", "kind", "project", "screenshots",
}
DASHBOARD_SCREENSHOT_FIELDS = {
    "bottomEvidence",
    "documentHeight",
    "filename",
    "imageHeight",
    "imageWidth",
    "path",
    "viewportHeight",
    "viewportWidth",
}


class StepFailed(Exception):
    """A gate assertion failed without exposing sensitive material."""


def _png_dimensions(content: bytes) -> Tuple[int, int]:
    if len(content) < 24 or not content.startswith(PNG_SIGNATURE) or content[12:16] != b"IHDR":
        raise StepFailed("dashboard screenshot is not a PNG with an IHDR")
    width, height = struct.unpack(">II", content[16:24])
    if width < 1 or height < 1:
        raise StepFailed("dashboard screenshot has invalid dimensions")
    return width, height


def expect(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print("  [ok] " + label, file=sys.stderr)
        return
    suffix = ": " + detail if detail else ""
    print("  [FAIL] " + label + suffix, file=sys.stderr)
    raise StepFailed(label)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_token(secret: str, subject: str, tenant_id: str, roles: List[str]) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {
        "sub": subject,
        "tenant_id": tenant_id,
        "roles": roles,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = (header + "." + payload).encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return header + "." + payload + "." + _b64url(signature)


def jwt_secret() -> str:
    secret = os.environ.get("PLATFORM_JWT_HMAC_SECRET", "")
    if len(secret.encode()) < 32:
        raise SystemExit("PLATFORM_JWT_HMAC_SECRET must be set (at least 32 bytes)")
    return secret


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.status, _json_or_text(response.read())
        except HTTPError as error:
            return error.code, _json_or_text(error.read())


def _json_or_text(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode("utf-8", "replace")[:300]


def _safe_http_detail(status: int, body: Any) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    return "http {}{}".format(status, " error=" + str(error) if error else "")


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _walk_artifact_files(root: Path) -> Iterator[Path]:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise StepFailed("test artifact root could not be statted safely") from error

    if stat.S_ISREG(root_stat.st_mode):
        yield root
        return
    if not stat.S_ISDIR(root_stat.st_mode):
        raise StepFailed("test artifact root has an unsupported file type")

    yield from _walk_artifact_directory(root)


def _walk_artifact_directory(directory: Path) -> Iterator[Path]:
    try:
        with os.scandir(directory) as scanner:
            entries = list(scanner)
    except OSError as error:
        raise StepFailed("test artifact directory could not be traversed safely") from error

    for entry in entries:
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise StepFailed("test artifact entry could not be statted safely") from error

        path = Path(entry.path)
        if stat.S_ISDIR(entry_stat.st_mode):
            yield from _walk_artifact_directory(path)
        elif stat.S_ISREG(entry_stat.st_mode):
            yield path
        else:
            raise StepFailed("test artifact entry has an unsupported file type")


def scrub_sensitive_artifacts(secret_file: Path, paths: List[Path], remove_media: bool) -> None:
    try:
        secret = secret_file.read_bytes().strip()
    except OSError as error:
        raise StepFailed("browser credential is unavailable for artifact scanning") from error
    if not secret:
        raise StepFailed("browser credential is empty during artifact scanning")

    removed_sensitive = False
    secret_path = Path(os.path.abspath(secret_file))
    for root in paths:
        for candidate in _walk_artifact_files(root):
            if Path(os.path.abspath(candidate)) == secret_path:
                continue
            remove_for_failure = remove_media and candidate.suffix.lower() in SENSITIVE_MEDIA_SUFFIXES
            if remove_for_failure:
                try:
                    candidate.unlink()
                except OSError as error:
                    raise StepFailed("sensitive test artifact could not be removed") from error
                continue
            try:
                contains_secret = secret in candidate.read_bytes()
            except OSError as error:
                raise StepFailed("test artifact could not be read safely") from error
            if contains_secret:
                try:
                    candidate.unlink()
                except OSError as error:
                    raise StepFailed("sensitive test artifact could not be removed") from error
                removed_sensitive = removed_sensitive or contains_secret
    if removed_sensitive:
        raise StepFailed("sensitive test artifact was removed")


def _artifact_destination(destination: Path, allowed_root: Path) -> Path:
    resolved_root = allowed_root.resolve()
    resolved_destination = destination.resolve()
    if (
        resolved_destination.parent != resolved_root
        or resolved_destination.name != "ci-artifacts"
    ):
        raise StepFailed("sanitized evidence destination is outside the allowlisted root")
    return resolved_destination


def _clear_sanitized_evidence(destination: Path, allowed_root: Path) -> Path:
    resolved_destination = _artifact_destination(destination, allowed_root)
    if resolved_destination.exists():
        shutil.rmtree(resolved_destination)
    return resolved_destination


def _decoded_bytes(raw: bytes) -> Iterator[bytes]:
    current = raw
    yield current
    for _ in range(3):
        decoded = unquote_to_bytes(current)
        if decoded == current:
            break
        current = decoded
        yield current


def _bytes_contain_secret(raw: bytes, secret: bytes) -> bool:
    for candidate in _decoded_bytes(raw):
        if secret in candidate:
            return True
        try:
            normalized = unicodedata.normalize("NFKC", candidate.decode("utf-8")).encode("utf-8")
        except UnicodeDecodeError:
            continue
        if secret in normalized:
            return True
    return False


def _value_contains_secret(value: Any, secret: bytes) -> bool:
    if isinstance(value, str):
        return _bytes_contain_secret(value.encode("utf-8"), secret)
    if isinstance(value, dict):
        return any(
            _value_contains_secret(key, secret) or _value_contains_secret(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_value_contains_secret(item, secret) for item in value)
    return False


def _validate_sanitized_network(network: Any) -> List[Dict[str, Any]]:
    if not isinstance(network, list) or len(network) > MAX_SANITIZED_NETWORK_EVENTS:
        raise StepFailed("sanitized network sequence exceeds its allowlisted shape")
    for event in network:
        if not isinstance(event, dict) or set(event) != {"method", "status", "url"}:
            raise StepFailed("sanitized network event uses an unexpected schema")
        method = event.get("method")
        status_code = event.get("status")
        url = event.get("url")
        if (
            method not in SANITIZED_NETWORK_METHODS
            or not isinstance(status_code, int)
            or isinstance(status_code, bool)
            or not 100 <= status_code <= 599
            or not isinstance(url, str)
            or len(url) > MAX_SANITIZED_NETWORK_URL_LENGTH
            or "%" in url
            or "\\" in url
        ):
            raise StepFailed("sanitized network event contains a non-allowlisted value")
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise StepFailed("sanitized network event URL is invalid") from error
        path_segments = parsed.path.split("/")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
            or not parsed.path.startswith("/")
            or any(segment in {".", ".."} for segment in path_segments)
        ):
            raise StepFailed("sanitized network event URL is not allowlisted")
    return network


def _validate_sanitized_trace(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise StepFailed("sanitized trace summary must be a JSON object")
    if set(payload) != SANITIZED_TRACE_FIELDS:
        raise StepFailed("sanitized trace summary uses an unexpected schema")
    project = payload.get("project")
    test_id = payload.get("testId")
    duration = payload.get("durationMs")
    last_path = payload.get("lastPath")
    valid = (
        payload.get("schemaVersion") == 1
        and payload.get("kind") == "sanitized-playwright-trace-summary"
        and project in SANITIZED_PROJECT_TESTS
        and SANITIZED_PROJECT_TESTS.get(project) == test_id
        and payload.get("status") in SANITIZED_STATUSES
        and isinstance(duration, int)
        and not isinstance(duration, bool)
        and duration >= 0
        and isinstance(last_path, str)
        and ADMIN_WEB_PATH.fullmatch(last_path) is not None
    )
    if not valid:
        raise StepFailed("sanitized trace summary contains a non-allowlisted value")
    _validate_sanitized_network(payload.get("network"))
    return payload


def export_sanitized_failure_evidence(
    secret_file: Path,
    source: Path,
    destination: Path,
    allowed_root: Path,
    additional_secret_files: Optional[List[Path]] = None,
) -> int:
    resolved_destination = _clear_sanitized_evidence(destination, allowed_root)
    secrets = []
    for credential in [secret_file, *(additional_secret_files or [])]:
        try:
            secret = credential.read_bytes().strip()
        except OSError as error:
            raise StepFailed("browser credential is unavailable for evidence export") from error
        if not secret:
            raise StepFailed("browser credential is empty during evidence export")
        secrets.append(secret)

    def contains_secret(raw: bytes) -> bool:
        return any(_bytes_contain_secret(raw, secret) for secret in secrets)

    def value_contains_secret(value: Any) -> bool:
        return any(_value_contains_secret(value, secret) for secret in secrets)

    summaries: List[Tuple[Path, Dict[str, Any]]] = []
    seen = set()
    try:
        for candidate in _walk_artifact_files(source):
            if candidate.name != "sanitized-trace-summary.json":
                continue
            raw = candidate.read_bytes()
            if contains_secret(raw):
                raise StepFailed("sanitized trace summary contains the browser credential")
            payload = _validate_sanitized_trace(json.loads(raw.decode("utf-8")))
            if value_contains_secret(payload):
                raise StepFailed("decoded sanitized trace summary contains the browser credential")
            identity = (payload["project"], payload["testId"])
            if identity in seen:
                raise StepFailed("sanitized trace summary identity is duplicated")
            seen.add(identity)
            summaries.append((candidate, payload))

        exports: List[Tuple[str, bytes | Dict[str, Any]]] = []
        for summary_path, payload in summaries:
            prefix = "{}-{}".format(payload["project"], payload["testId"])
            exports.append((prefix + "-sanitized-trace-summary.json", payload))
            screenshot = summary_path.parent / "sanitized-failure.png"
            if screenshot.is_file():
                screenshot_bytes = screenshot.read_bytes()
                if (
                    not screenshot_bytes.startswith(PNG_SIGNATURE)
                    or contains_secret(screenshot_bytes)
                ):
                    raise StepFailed("sanitized failure screenshot did not pass validation")
                exports.append((prefix + "-sanitized-failure.png", screenshot_bytes))

        manifests = [
            candidate for candidate in _walk_artifact_files(source)
            if candidate.name == "dashboard-screenshot-manifest.json"
        ]
        if len(manifests) > 1:
            raise StepFailed("dashboard screenshot manifest is duplicated")
        if manifests:
            manifest_path = manifests[0]
            manifest_bytes = manifest_path.read_bytes()
            if contains_secret(manifest_bytes):
                raise StepFailed("dashboard screenshot manifest contains the browser credential")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if not isinstance(manifest, dict) or set(manifest) != DASHBOARD_SCREENSHOT_MANIFEST_FIELDS:
                raise StepFailed("dashboard screenshot manifest uses an unexpected schema")
            screenshots = manifest.get("screenshots")
            valid_manifest = (
                manifest.get("schemaVersion") == 1
                and manifest.get("kind") == "sanitized-dashboard-screenshot-set"
                and manifest.get("project") == "chromium-dashboard"
                and isinstance(screenshots, list)
                and len(screenshots) == len(DASHBOARD_SCREENSHOT_WIDTHS)
            )
            if not valid_manifest:
                raise StepFailed("dashboard screenshot manifest contains a non-allowlisted value")
            observed_widths = []
            for item in screenshots:
                if not isinstance(item, dict) or set(item) != DASHBOARD_SCREENSHOT_FIELDS:
                    raise StepFailed("dashboard screenshot entry uses an unexpected schema")
                width = item.get("viewportWidth")
                filename = item.get("filename")
                if (
                    width not in DASHBOARD_SCREENSHOT_WIDTHS
                    or item.get("viewportHeight") != 900
                    or not isinstance(item.get("documentHeight"), int)
                    or isinstance(item.get("documentHeight"), bool)
                    or item.get("documentHeight") < item.get("viewportHeight")
                    or item.get("imageWidth") != width
                    or not isinstance(item.get("imageHeight"), int)
                    or isinstance(item.get("imageHeight"), bool)
                    or item.get("imageHeight") < item.get("documentHeight")
                    or item.get("bottomEvidence") != "最近工作"
                    or item.get("path") != "/dashboard"
                    or filename != "dashboard-{}.png".format(width)
                ):
                    raise StepFailed("dashboard screenshot entry contains a non-allowlisted value")
                observed_widths.append(width)
                screenshot_path = manifest_path.parent / filename
                screenshot_bytes = screenshot_path.read_bytes()
                image_width, image_height = _png_dimensions(screenshot_bytes)
                if (
                    image_width != item.get("imageWidth")
                    or image_height != item.get("imageHeight")
                    or image_height < item.get("documentHeight")
                    or contains_secret(screenshot_bytes)
                ):
                    raise StepFailed("dashboard screenshot did not pass validation")
                exports.append(("chromium-dashboard-" + filename, screenshot_bytes))
            if sorted(observed_widths) != list(DASHBOARD_SCREENSHOT_WIDTHS):
                raise StepFailed("dashboard screenshot widths are incomplete or duplicated")
            exports.append((
                "chromium-dashboard-dashboard-screenshot-manifest.json",
                manifest,
            ))

        root_ids = [
            candidate for candidate in _walk_artifact_files(source)
            if candidate.name == "run-root-resource-id.txt"
        ]
        if len(root_ids) > 1:
            raise StepFailed("run root resource evidence is duplicated")
        if root_ids:
            root_bytes = root_ids[0].read_bytes()
            if contains_secret(root_bytes):
                raise StepFailed("run root resource evidence contains the browser credential")
            try:
                root_id = root_bytes.decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise StepFailed("run root resource evidence is not ASCII") from error
            if not _valid_uuid(root_id):
                raise StepFailed("run root resource evidence is not a canonical UUID")
            exports.append(("run-root-resource-id.txt", (root_id + "\n").encode("ascii")))

        if not exports:
            return 0
        resolved_destination.mkdir(parents=True, exist_ok=False)
        for filename, content in exports:
            output = resolved_destination / filename
            if isinstance(content, dict):
                _write_json(output, content)
            else:
                output.write_bytes(content)
            if contains_secret(output.read_bytes()):
                raise StepFailed("final sanitized evidence contains the browser credential")
        return len(exports)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        _clear_sanitized_evidence(destination, allowed_root)
        raise StepFailed("sanitized failure evidence could not be validated") from error
    except StepFailed:
        _clear_sanitized_evidence(destination, allowed_root)
        raise


def _load_object(path: Path, label: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StepFailed("{} is not readable JSON: {}".format(label, type(error).__name__)) from error
    if not isinstance(payload, dict):
        raise StepFailed(label + " must be a JSON object")
    return payload


def _valid_uuid(value: Any) -> bool:
    try:
        return str(uuid.UUID(str(value))) == str(value)
    except (ValueError, TypeError, AttributeError):
        return False


def validate_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    expect(set(payload) == RESULT_FIELDS, "result uses the exact schema")
    expect(_valid_uuid(payload.get("rootProjectId")), "result rootProjectId is a canonical UUID")
    expect(_valid_uuid(payload.get("folderId")), "result folderId is a canonical UUID")
    expect(_valid_uuid(payload.get("surveyId")), "result surveyId is a canonical UUID")
    expect(_valid_uuid(payload.get("tenantId")), "result tenantId is a canonical UUID")
    expect(payload.get("version") == 1, "result identifies published version 1")
    network = payload.get("network")
    expect(isinstance(network, list) and len(network) > 0, "result carries redacted network metadata")
    for record in network:
        valid_record = isinstance(record, dict) and set(record) == {"method", "status", "url"}
        if valid_record:
            parsed = urlsplit(record["url"])
            valid_record = (
                record["method"] in {"GET", "POST", "PUT", "DELETE", "PATCH"}
                and isinstance(record["status"], int)
                and 100 <= record["status"] <= 599
                and parsed.scheme in {"http", "https"}
                and bool(parsed.netloc)
                and not parsed.fragment
            )
        expect(valid_record, "network metadata contains only method, status and URL")
    return payload


def archive_run_root(api: Api, root_id: str) -> None:
    if not _valid_uuid(root_id):
        raise StepFailed("run root resource ID is not a canonical UUID")
    status, resource = api.call("POST", "/v1/resources/{}/archive".format(root_id))
    valid = (
        status == 200
        and isinstance(resource, dict)
        and resource.get("id") == root_id
        and resource.get("kind") == "project"
        and isinstance(resource.get("archivedAt"), str)
        and bool(resource["archivedAt"])
    )
    expect(valid, "successful browser suite archived its run root")


def validate_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    expected = {"schemaVersion", "tenantId", "actorId", "engineInstanceId"}
    expect(set(payload) == expected, "metadata uses the exact non-sensitive schema")
    expect(payload.get("schemaVersion") == 1, "metadata schema version is 1")
    expect(_valid_uuid(payload.get("tenantId")), "metadata tenantId is a canonical UUID")
    expect(payload.get("actorId") == OWNER_ACTOR, "metadata identifies the E2E owner")
    expect(
        isinstance(payload.get("engineInstanceId"), str) and bool(payload["engineInstanceId"]),
        "metadata identifies the engine instance",
    )
    return payload


def validate_evidence(
    metadata: Dict[str, Any],
    result: Dict[str, Any],
    api: Dict[str, Any],
    platform_db: Dict[str, Any],
    engine_db: Dict[str, Any],
    gateway_publish_calls: int,
) -> None:
    survey = api.get("survey") or {}
    versions = api.get("versions") or []
    route = api.get("route") or {}
    expected_version = next(
        (item for item in versions if isinstance(item, dict) and item.get("version") == result["version"]),
        {},
    )
    expect(result["tenantId"] == metadata["tenantId"], "browser result belongs to the seeded tenant")
    expect(
        survey.get("id") == result["surveyId"]
        and survey.get("status") == "published"
        and survey.get("publishedVersion") == result["version"],
        "platform API confirms the published survey and live version",
    )
    expect(
        expected_version.get("engineInstanceId") == metadata["engineInstanceId"]
        and isinstance(expected_version.get("engineSid"), int)
        and expected_version.get("engineSid") > 0
        and expected_version.get("live") is True
        and str(expected_version.get("fingerprint", "")).startswith("fm1:"),
        "platform API confirms the live binding and fingerprint",
    )
    expect(
        route.get("publicId") == result["surveyId"]
        and route.get("tenantId") == metadata["tenantId"]
        and route.get("engineInstanceId") == expected_version.get("engineInstanceId")
        and route.get("engineSid") == expected_version.get("engineSid"),
        "platform API confirms the public route",
    )
    expect(
        platform_db.get("status") == "published"
        and platform_db.get("publishedVersion") == result["version"]
        and platform_db.get("engineInstanceId") == expected_version.get("engineInstanceId")
        and platform_db.get("engineSid") == expected_version.get("engineSid")
        and platform_db.get("liveVersionCount") == 1,
        "platform database confirms exactly one current published binding",
    )
    expect(
        engine_db.get("active") == "Y" and engine_db.get("surveyCount") == 1,
        "engine database confirms the bound survey is active",
    )
    expect(gateway_publish_calls == 1, "gateway handled exactly one publish request")


def cmd_prepare(args: argparse.Namespace) -> None:
    secret = jwt_secret()
    operator = Api(args.base_url, mint_token(secret, OPERATOR_ACTOR, str(uuid.uuid4()), [OPERATOR_ROLE]))
    suffix = uuid.uuid4().hex[:8]
    status, tenant = operator.call(
        "POST", "/v1/platform/tenants", {"code": "admin-web-" + suffix, "name": "Admin Web E2E tenant"}
    )
    expect(status == 201 and isinstance(tenant, dict), "operator creates the E2E tenant", _safe_http_detail(status, tenant))
    tenant_id = tenant.get("id")
    expect(_valid_uuid(tenant_id), "created tenant has a canonical UUID")

    status, plan = operator.call("POST", "/v1/platform/plans", {
        "planCode": "admin-web-" + suffix,
        "capabilities": ["survey.read", "survey.write", "response.collect"],
        "quotas": {"member.seats": 5, "response.valid_completed": 1000},
        "exportWindowDays": 30,
    })
    expect(status == 201 and isinstance(plan, dict) and plan.get("version") == 1,
           "operator publishes the E2E plan", _safe_http_detail(status, plan))

    status, onboarding = operator.call(
        "POST", "/v1/platform/tenants/{}/onboarding".format(tenant_id),
        {"planVersionId": plan.get("id"), "kind": "TRIAL", "days": 1, "ownerActorId": OWNER_ACTOR},
    )
    expect(status == 200 and isinstance(onboarding, dict) and onboarding.get("ownerActorId") == OWNER_ACTOR,
           "operator onboards the E2E owner", _safe_http_detail(status, onboarding))

    status, activated = operator.call(
        "POST", "/v1/platform/tenants/{}/status".format(tenant_id), {"status": "active"}
    )
    expect(status == 200 and isinstance(activated, dict) and activated.get("status") == "active",
           "operator activates the E2E tenant", _safe_http_detail(status, activated))

    status, instance = operator.call(
        "POST", "/v1/platform/tenants/{}/engine-instances".format(tenant_id),
        {"id": args.instance, "baseUrl": args.engine_base_url},
    )
    expect(status == 201 and isinstance(instance, dict) and instance.get("id") == args.instance,
           "operator registers the E2E engine", _safe_http_detail(status, instance))

    status, issued = operator.call("POST", "/v1/platform/engine-instances/{}/event-secret".format(args.instance))
    event_secret = issued.get("secret") if isinstance(issued, dict) else None
    expect(status == 200 and isinstance(event_secret, str) and len(event_secret) == 64,
           "operator issues the engine event secret (value withheld)", "http {}".format(status))
    write_private(Path(args.event_secret_file), event_secret)

    _write_json(Path(args.metadata_file), {
        "schemaVersion": 1,
        "tenantId": tenant_id,
        "actorId": OWNER_ACTOR,
        "engineInstanceId": args.instance,
    })
    print("  [ok] non-sensitive browser metadata prepared", file=sys.stderr)


def cmd_issue_browser_token(args: argparse.Namespace) -> None:
    metadata = validate_metadata(_load_object(Path(args.metadata_file), "metadata"))
    actor_id = getattr(args, "actor_id", None) or metadata["actorId"]
    if actor_id not in TEST_BROWSER_ACTORS:
        raise StepFailed("browser credential actor is not allowlisted")
    browser_token = mint_token(jwt_secret(), actor_id, metadata["tenantId"], [])
    write_private(Path(args.jwt_file), browser_token)
    print("  [ok] short-lived browser credential issued (value withheld)", file=sys.stderr)


def cmd_scan_artifacts(args: argparse.Namespace) -> None:
    scrub_sensitive_artifacts(
        Path(args.jwt_file),
        [Path(path) for path in args.path],
        remove_media=args.remove_media,
    )


def cmd_export_sanitized_evidence(args: argparse.Namespace) -> None:
    secret_files = [Path(path) for path in args.jwt_file]
    count = export_sanitized_failure_evidence(
        secret_files[0],
        Path(args.source_dir),
        Path(args.output_dir),
        Path(args.allowed_root),
        additional_secret_files=secret_files[1:],
    )
    print("  [ok] exported {} sanitized failure evidence files".format(count), file=sys.stderr)


def cmd_archive_run_root(args: argparse.Namespace) -> None:
    try:
        token = Path(args.jwt_file).read_text(encoding="utf-8").strip()
        root_id = Path(args.root_id_file).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise StepFailed("run root archive inputs are unavailable") from error
    if not token:
        raise StepFailed("browser credential is empty during run root archive")
    archive_run_root(Api(args.base_url, token), root_id)


def _run(command: List[str], input_text: Optional[str] = None) -> str:
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise StepFailed("external verification command failed: {}".format(command[0]))
    return completed.stdout.strip()


def _platform_db_evidence(container: str, database: str, tenant_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    sql = r"""
BEGIN;
SELECT set_config('app.tenant_id', '{tenant}', true) \g /dev/null
SELECT json_build_object(
  'status', s.status,
  'publishedVersion', s.published_version,
  'engineInstanceId', v.engine_instance_id,
  'engineSid', v.engine_sid,
  'liveVersionCount', (
    SELECT count(*) FROM survey_published_version live
     WHERE live.tenant_id = s.tenant_id AND live.survey_id = s.id
       AND live.version_no = s.published_version
  )
)::text
FROM survey s
JOIN survey_published_version v
  ON v.tenant_id = s.tenant_id AND v.survey_id = s.id AND v.version_no = s.published_version
WHERE s.id = '{survey}';
COMMIT;
""".format(tenant=tenant_id, survey=result["surveyId"])
    output = _run([
        "docker", "exec", "-i", container, "psql", "-U", "platform_owner", "-d", database,
        "-v", "ON_ERROR_STOP=1", "-tAq",
    ], sql)
    objects = [line for line in output.splitlines() if line.lstrip().startswith("{")]
    if len(objects) != 1:
        raise StepFailed("platform database returned no unique published binding")
    return json.loads(objects[0])


def _engine_db_evidence(container: str, kind: str, sid: int) -> Dict[str, Any]:
    query = "SELECT active, COUNT(*) FROM lime_surveys WHERE sid = {} GROUP BY active;".format(sid)
    if kind == "pgsql":
        output = _run(["docker", "exec", container, "psql", "-U", "postgres", "-d", "limesurvey", "-tAq", "-F", "|", "-c", query])
    else:
        output = _run(["docker", "exec", container, "mariadb", "-uroot", "-proot", "limesurvey", "-N", "-B", "-e", query])
        output = output.replace("\t", "|")
    fields = output.strip().split("|")
    if len(fields) != 2 or not fields[1].isdigit():
        raise StepFailed("engine database returned no unique survey row")
    return {"active": fields[0], "surveyCount": int(fields[1])}


def _gateway_publish_calls(container: str) -> int:
    completed = subprocess.run(
        ["docker", "logs", container],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise StepFailed("gateway logs are unavailable")
    return count_gateway_publish_calls(completed.stdout)


def count_gateway_publish_calls(logs: str) -> int:
    return sum(1 for line in logs.splitlines() if '"POST /v1/publish' in line)


def cmd_verify(args: argparse.Namespace) -> None:
    metadata = validate_metadata(_load_object(Path(args.metadata_file), "metadata"))
    result = validate_result(_load_object(Path(args.result_file), "browser result"))
    owner = Api(args.base_url, mint_token(jwt_secret(), metadata["actorId"], metadata["tenantId"], []))

    status, survey = owner.call("GET", "/v1/surveys/{}".format(result["surveyId"]))
    expect(status == 200 and isinstance(survey, dict), "verification reads the survey through the platform API", "http {}".format(status))
    status, versions = owner.call("GET", "/v1/surveys/{}/versions".format(result["surveyId"]))
    expect(status == 200 and isinstance(versions, list), "verification reads versions through the platform API", "http {}".format(status))
    status, route = owner.call("GET", "/v1/survey-routes/{}".format(result["surveyId"]))
    expect(status == 200 and isinstance(route, dict), "verification reads the public route through the platform API", "http {}".format(status))

    version = next(
        (item for item in versions if isinstance(item, dict) and item.get("version") == result["version"]),
        {},
    )
    expect(
        isinstance(version.get("engineSid"), int) and version["engineSid"] > 0,
        "platform API supplies the engine sid for database verification",
    )
    platform_db = _platform_db_evidence(
        args.platform_db_container, args.platform_db_name, metadata["tenantId"], result
    )
    engine_db = _engine_db_evidence(args.engine_db_container, args.engine_db_kind, version["engineSid"])
    validate_evidence(
        metadata,
        result,
        {"survey": survey, "versions": versions, "route": route},
        platform_db,
        engine_db,
        _gateway_publish_calls(args.gateway_container),
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--instance", required=True)
    prepare.add_argument("--engine-base-url", required=True)
    prepare.add_argument("--metadata-file", required=True)
    prepare.add_argument("--event-secret-file", required=True)
    prepare.set_defaults(handler=cmd_prepare)

    issue = commands.add_parser("issue-browser-token")
    issue.add_argument("--metadata-file", required=True)
    issue.add_argument("--jwt-file", required=True)
    issue.add_argument("--actor-id", choices=sorted(TEST_BROWSER_ACTORS))
    issue.set_defaults(handler=cmd_issue_browser_token)

    scan = commands.add_parser("scan-artifacts")
    scan.add_argument("--jwt-file", required=True)
    scan.add_argument("--path", action="append", required=True)
    scan.add_argument("--remove-media", action="store_true")
    scan.set_defaults(handler=cmd_scan_artifacts)

    export = commands.add_parser("export-sanitized-evidence")
    export.add_argument("--jwt-file", action="append", required=True)
    export.add_argument("--source-dir", required=True)
    export.add_argument("--output-dir", required=True)
    export.add_argument("--allowed-root", required=True)
    export.set_defaults(handler=cmd_export_sanitized_evidence)

    archive = commands.add_parser("archive-run-root")
    archive.add_argument("--jwt-file", required=True)
    archive.add_argument("--root-id-file", required=True)
    archive.set_defaults(handler=cmd_archive_run_root)

    verify = commands.add_parser("verify")
    verify.add_argument("--metadata-file", required=True)
    verify.add_argument("--result-file", required=True)
    verify.add_argument("--platform-db-container", required=True)
    verify.add_argument("--platform-db-name", default="platform")
    verify.add_argument("--engine-db-container", required=True)
    verify.add_argument("--engine-db-kind", choices=("mysql", "pgsql"), required=True)
    verify.add_argument("--gateway-container", required=True)
    verify.set_defaults(handler=cmd_verify)
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        args.handler(args)
    except StepFailed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
