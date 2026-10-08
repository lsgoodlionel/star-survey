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
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


TOKEN_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 300
OPERATOR_ACTOR = "admin-web-e2e-operator"
OWNER_ACTOR = "admin-web-e2e-owner"
OPERATOR_ROLE = "platform_operator"
RESULT_FIELDS = {
    "surveyId",
    "tenantId",
    "version",
    "network",
}


class StepFailed(Exception):
    """A gate assertion failed without exposing sensitive material."""


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


def write_prepare_artifacts(
    jwt_path: Path,
    metadata_path: Path,
    token: str,
    metadata: Dict[str, Any],
) -> None:
    expect("jwt" not in {key.lower() for key in metadata}, "metadata excludes JWT fields")
    serialized = json.dumps(metadata, ensure_ascii=False)
    expect(token not in serialized, "metadata excludes token value")
    write_private(jwt_path, token)
    _write_json(metadata_path, metadata)
    print("  [ok] private browser credential prepared (value withheld)", file=sys.stderr)


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

    owner_token = mint_token(secret, OWNER_ACTOR, tenant_id, [])
    write_prepare_artifacts(Path(args.jwt_file), Path(args.metadata_file), owner_token, {
        "schemaVersion": 1,
        "tenantId": tenant_id,
        "actorId": OWNER_ACTOR,
        "engineInstanceId": args.instance,
    })


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
    prepare.add_argument("--jwt-file", required=True)
    prepare.add_argument("--metadata-file", required=True)
    prepare.add_argument("--event-secret-file", required=True)
    prepare.set_defaults(handler=cmd_prepare)

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
