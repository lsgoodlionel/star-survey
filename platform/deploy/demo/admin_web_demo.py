#!/usr/bin/env python3

"""Seed and maintain the isolated local admin-web demo environment."""

import argparse
import base64
import hashlib
import hmac
import json
import os
import stat
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


COMPOSE_PROJECT = "adminweb-demo"
TENANT_CODE = "admin-web-demo"
TENANT_NAME = "Admin Web Demo tenant"
OWNER_ACTOR = "admin-web-demo-owner"
OPERATOR_ACTOR = "admin-web-demo-operator"
OPERATOR_ROLE = "platform_operator"
ENGINE_INSTANCE = "admin-web-demo-engine-01"
PROJECT_NAME = "客户体验研究"
FOLDER_NAME = "2026 Q4"
SURVEY_NAME = "品牌跟踪调查"
TOKEN_TTL_SECONDS = 8 * 60 * 60
HTTP_TIMEOUT_SECONDS = 300


class DemoError(Exception):
    """A demo lifecycle assertion failed without exposing credentials."""


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = token

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self._base + path, data=raw, headers=headers, method=method)
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
        return raw.decode("utf-8", "replace")[:200]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_token(secret: str, subject: str, tenant_id: str, roles=None) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {
        "sub": subject,
        "tenant_id": tenant_id,
        "roles": list(roles or []),
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = (header + "." + payload).encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return header + "." + payload + "." + _b64url(signature)


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


def _write_metadata(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_access(path: Path, payload: Dict[str, Any]) -> None:
    write_private(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(path)


def _load_object(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DemoError("demo metadata is unavailable or invalid") from error
    if not isinstance(payload, dict):
        raise DemoError("demo metadata must be an object")
    return payload


def _require(status: int, payload: Any, expected: int, label: str) -> Dict[str, Any]:
    if status != expected or not isinstance(payload, dict):
        error = payload.get("error") if isinstance(payload, dict) else None
        suffix = " ({})".format(error) if error else ""
        raise DemoError("{} failed with HTTP {}{}".format(label, status, suffix))
    return payload


def ssl_policy(environment: str, public_url: str = "", https_redirect: bool = False,
               proxy_headers: bool = False, secure_cookie: bool = False) -> Dict[str, Any]:
    if environment in {"local", "demo", "ci"}:
        return {"force_ssl": "off", "ssl_disable_alert": 1}
    if environment != "production":
        raise DemoError("unknown SSL environment")
    if urlsplit(public_url).scheme != "https":
        raise DemoError("production force_ssl requires an HTTPS public URL")
    if not (https_redirect and proxy_headers and secure_cookie):
        raise DemoError("production force_ssl requires HTTPS redirect, proxy header and secure cookie verification")
    return {"force_ssl": "on", "ssl_disable_alert": 0}


def demo_definition() -> Dict[str, Any]:
    settings = {
        "anonymized": "N", "datestamp": "Y", "savetimings": "N", "ipaddr": "N", "refurl": "N",
        "allowsave": "N", "allowprev": "Y", "alloweditaftercompletion": "N", "format": "G",
        "questionindex": "0",
    }
    return {
        "definitionVersion": 1,
        "uuid": "admin-web-demo-definition-v1",
        "title": SURVEY_NAME,
        "language": "zh-Hans",
        "theme": "zh-business",
        "settings": settings,
        "branding": {
            "brandingVersion": 1,
            "primaryColor": "#0F766E",
            "pageTitle": SURVEY_NAME,
            "footerText": "客户体验研究",
        },
        "groups": [
            {
                "uuid": "11111111-1111-4111-8111-111111111111",
                "title": "品牌认知",
                "questions": [
                    {
                        "uuid": "31111111-1111-4111-8111-111111111111", "code": "QBRAND", "type": "L",
                        "text": "您最熟悉哪个品牌？", "mandatory": True,
                        "answers": [{"code": "A1", "text": "甲品牌"}, {"code": "A2", "text": "乙品牌"}],
                    },
                    {
                        "uuid": "32222222-2222-4222-8222-222222222222", "code": "QCHANNEL", "type": "M",
                        "text": "您通过哪些渠道了解品牌？",
                        "subquestions": [
                            {"uuid": "41111111-1111-4111-8111-111111111111", "code": "SOCIAL", "text": "社交媒体"},
                            {"uuid": "42222222-2222-4222-8222-222222222222", "code": "STORE", "text": "线下门店"},
                        ],
                    },
                    {"uuid": "33333333-3333-4333-8333-333333333333", "code": "QWORD", "type": "S", "text": "请用一个词描述该品牌。"},
                    {"uuid": "34444444-4444-4444-8444-444444444444", "code": "QNOTE", "type": "T", "text": "您还有哪些建议？"},
                ],
            },
            {
                "uuid": "22222222-2222-4222-8222-222222222222",
                "title": "进阶展示",
                "questions": [{
                    "uuid": "35555555-5555-4555-8555-555555555555", "code": "QSECTION", "type": "X",
                    "text": "下面进入购买体验部分。", "theme": "mjy-collapsible",
                    "themeOptions": {"summary": "购买体验"},
                }],
            },
        ],
    }


def _valid_resource(payload: Any, resource_id: str, kind: str, name: str,
                    parent_id: Optional[str]) -> bool:
    return isinstance(payload, dict) and payload.get("id") == resource_id \
        and payload.get("kind") == kind and payload.get("name") == name \
        and payload.get("parentId") == parent_id


def _get_resource(api: Api, resource_id: str, kind: str, name: str,
                  parent_id: Optional[str]) -> bool:
    status, payload = api.call("GET", "/v1/resources/{}".format(resource_id))
    return status == 200 and _valid_resource(payload, resource_id, kind, name, parent_id)


def _published_version(api: Api, survey_id: str, survey: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if survey.get("status") != "published" or survey.get("publishedVersion") != 1:
        return None
    status, versions = api.call("GET", "/v1/surveys/{}/versions".format(survey_id))
    if status != 200 or not isinstance(versions, list):
        return None
    return next(
        (item for item in versions if isinstance(item, dict) and item.get("version") == 1
         and item.get("live") is True and isinstance(item.get("engineSid"), int)),
        None,
    )


def ensure_demo_seed(operator: Api, owner: Api, metadata: Dict[str, Any],
                     engine_operations_url: str, metadata_path: Optional[Path] = None) -> Dict[str, Any]:
    tenant_id = metadata.get("tenantId")
    status, tenant = operator.call("GET", "/v1/platform/tenants/{}".format(tenant_id))
    if status != 200 or not isinstance(tenant, dict) or tenant.get("code") != TENANT_CODE \
            or tenant.get("status") != "active":
        raise DemoError("persisted demo tenant could not be validated")

    project_id = metadata.get("projectId")
    if not project_id or not _get_resource(owner, project_id, "project", PROJECT_NAME, None):
        project = _require(*owner.call("POST", "/v1/projects", {"name": PROJECT_NAME}), 201, "create demo project")
        project_id = project.get("id")
        metadata.update({"projectId": project_id, "folderId": None, "surveyId": None})
        if metadata_path is not None:
            _write_metadata(metadata_path, metadata)

    folder_id = metadata.get("folderId")
    if not folder_id or not _get_resource(owner, folder_id, "folder", FOLDER_NAME, project_id):
        folder = _require(
            *owner.call("POST", "/v1/folders", {"parentId": project_id, "name": FOLDER_NAME}),
            201, "create demo folder",
        )
        folder_id = folder.get("id")
        metadata.update({"folderId": folder_id, "surveyId": None})
        if metadata_path is not None:
            _write_metadata(metadata_path, metadata)

    survey_id = metadata.get("surveyId")
    survey = None
    if survey_id and _get_resource(owner, survey_id, "survey", SURVEY_NAME, folder_id):
        status, candidate = owner.call("GET", "/v1/surveys/{}".format(survey_id))
        if status == 200 and isinstance(candidate, dict) and candidate.get("title") == SURVEY_NAME:
            survey = candidate
    if survey is None:
        survey = _require(
            *owner.call("POST", "/v1/surveys", {"parentId": folder_id, "definition": demo_definition()}),
            201, "create demo survey",
        )
        survey_id = survey.get("id")
        metadata["surveyId"] = survey_id
        if metadata_path is not None:
            _write_metadata(metadata_path, metadata)

    version = _published_version(owner, survey_id, survey)
    if version is None:
        outcome = _require(
            *owner.call("POST", "/v1/surveys/{}/publish".format(survey_id)),
            200, "publish demo survey",
        )
        published = outcome.get("survey")
        version = outcome.get("version")
        if not isinstance(published, dict) or published.get("status") != "published" \
                or not isinstance(version, dict) or version.get("version") != 1 \
                or version.get("live") is not True or not isinstance(version.get("engineSid"), int):
            raise DemoError("demo survey publish did not produce a live version")

    respondent_url = "{}/index.php/{}?newtest=Y&lang=zh-Hans".format(
        engine_operations_url.rstrip("/"), version["engineSid"]
    )
    return {"metadata": metadata, "respondentUrl": respondent_url}


def _bootstrap(operator: Api, engine_internal_url: str, metadata_path: Path,
               event_secret_path: Path) -> Dict[str, Any]:
    if metadata_path.exists():
        metadata = _load_object(metadata_path)
        status, tenant = operator.call("GET", "/v1/platform/tenants/{}".format(metadata.get("tenantId")))
        if status != 200 or not isinstance(tenant, dict) or tenant.get("code") != TENANT_CODE:
            raise DemoError("persisted demo tenant could not be validated")
        if not event_secret_path.exists():
            status, issued = operator.call(
                "POST", "/v1/platform/engine-instances/{}/event-secret".format(ENGINE_INSTANCE)
            )
            issued = _require(status, issued, 200, "issue demo engine event secret")
            secret = issued.get("secret")
            if not isinstance(secret, str) or len(secret) != 64:
                raise DemoError("engine event secret response is invalid")
            write_private(event_secret_path, secret)
        return metadata

    suffix = uuid.uuid4().hex[:8]
    tenant = _require(
        *operator.call("POST", "/v1/platform/tenants", {"code": TENANT_CODE, "name": TENANT_NAME}),
        201, "create demo tenant",
    )
    tenant_id = tenant.get("id")
    plan = _require(
        *operator.call("POST", "/v1/platform/plans", {
            "planCode": "admin-web-demo-" + suffix,
            "capabilities": ["survey.read", "survey.write", "response.collect"],
            "quotas": {"member.seats": 5, "response.valid_completed": 1000},
            "exportWindowDays": 30,
        }),
        201, "create demo plan",
    )
    _require(
        *operator.call("POST", "/v1/platform/tenants/{}/onboarding".format(tenant_id), {
            "planVersionId": plan.get("id"), "kind": "TRIAL", "days": 3650, "ownerActorId": OWNER_ACTOR,
        }),
        200, "onboard demo tenant",
    )
    _require(
        *operator.call("POST", "/v1/platform/tenants/{}/status".format(tenant_id), {"status": "active"}),
        200, "activate demo tenant",
    )
    _require(
        *operator.call("POST", "/v1/platform/tenants/{}/engine-instances".format(tenant_id), {
            "id": ENGINE_INSTANCE, "baseUrl": engine_internal_url,
        }),
        201, "register demo engine",
    )
    issued = _require(
        *operator.call("POST", "/v1/platform/engine-instances/{}/event-secret".format(ENGINE_INSTANCE)),
        200, "issue demo engine event secret",
    )
    secret = issued.get("secret")
    if not isinstance(secret, str) or len(secret) != 64:
        raise DemoError("engine event secret response is invalid")
    write_private(event_secret_path, secret)
    metadata = {
        "schemaVersion": 1, "tenantId": tenant_id, "projectId": None, "folderId": None,
        "surveyId": None, "engineInstanceId": ENGINE_INSTANCE,
    }
    _write_metadata(metadata_path, metadata)
    return metadata


def _secret() -> str:
    secret = os.environ.get("PLATFORM_JWT_HMAC_SECRET", "")
    if len(secret.encode()) < 32:
        raise DemoError("PLATFORM_JWT_HMAC_SECRET must contain at least 32 bytes")
    return secret


def _operator(base_url: str, secret: str) -> Api:
    return Api(base_url, mint_token(secret, OPERATOR_ACTOR, str(uuid.uuid4()), [OPERATOR_ROLE]))


def cmd_start(args: argparse.Namespace) -> None:
    secret = _secret()
    metadata_path = Path(args.metadata_file)
    event_secret_path = Path(args.event_secret_file)
    operator = _operator(args.platform_url, secret)
    metadata = _bootstrap(operator, args.engine_internal_url, metadata_path, event_secret_path)
    if args.prepare_only:
        return
    tenant_id = metadata["tenantId"]
    owner_token = mint_token(secret, OWNER_ACTOR, tenant_id)
    result = ensure_demo_seed(
        operator, Api(args.platform_url, owner_token), metadata, args.engine_operations_url,
        metadata_path=metadata_path,
    )
    _write_metadata(metadata_path, result["metadata"])
    write_access(Path(args.access_file), {
        "schemaVersion": 1,
        "adminWebUrl": args.admin_web_url,
        "engineOperationsUrl": args.engine_operations_url,
        "respondentUrl": result["respondentUrl"],
        "platformUsername": OWNER_ACTOR,
        "platformAccessToken": owner_token,
        "engineUsername": os.environ.get("DEMO_ENGINE_ADMIN_USER", "admin"),
        "enginePassword": os.environ.get("DEMO_ENGINE_ADMIN_PASSWORD", ""),
    })


def cmd_refresh_token(args: argparse.Namespace) -> None:
    secret = _secret()
    metadata = _load_object(Path(args.metadata_file))
    access = _load_object(Path(args.access_file))
    access["platformAccessToken"] = mint_token(secret, OWNER_ACTOR, metadata["tenantId"])
    write_access(Path(args.access_file), access)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated admin-web demo lifecycle support")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--platform-url")
    start.add_argument("--engine-internal-url", default="http://engine")
    start.add_argument("--engine-operations-url")
    start.add_argument("--admin-web-url")
    start.add_argument("--metadata-file")
    start.add_argument("--event-secret-file")
    start.add_argument("--access-file")
    start.add_argument("--prepare-only", action="store_true")
    commands.add_parser("stop")
    commands.add_parser("status")
    refresh = commands.add_parser("refresh-token")
    refresh.add_argument("--metadata-file")
    refresh.add_argument("--access-file")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "start":
            cmd_start(args)
        elif args.command == "refresh-token":
            cmd_refresh_token(args)
    except DemoError as error:
        print("demo lifecycle failed: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
