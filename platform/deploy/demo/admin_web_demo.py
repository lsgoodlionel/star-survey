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
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


COMPOSE_PROJECT = "adminweb-demo"
TENANT_CODE = "admin-web-demo"
TENANT_NAME = "Admin Web Demo tenant"
OWNER_ACTOR = "admin-web-demo-owner"
OPERATOR_ACTOR = "admin-web-demo-operator"
OPERATOR_ROLE = "platform_operator"
ENGINE_INSTANCE = "admin-web-demo-engine-01"
PLAN_CODE = "admin-web-demo"
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
        except (URLError, TimeoutError, OSError) as error:
            raise DemoError("platform API request failed") from error


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


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_private(path: Path, content: str) -> None:
    _atomic_write(path, content)


def _write_metadata(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


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


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _http_probe(url: str, headers: Dict[str, str]) -> Tuple[int, str, Tuple[str, ...]]:
    request = Request(url, headers=headers, method="GET")
    try:
        with build_opener(_NoRedirect).open(request, timeout=10) as response:
            return response.status, response.headers.get("Location", ""), tuple(
                response.headers.get_all("Set-Cookie") or []
            )
    except HTTPError as error:
        return error.code, error.headers.get("Location", ""), tuple(
            error.headers.get_all("Set-Cookie") or []
        )
    except (URLError, TimeoutError, OSError) as error:
        raise DemoError("production SSL probe failed") from error


def _https_redirect_target(public_url: str) -> str:
    parts = urlsplit(public_url)
    return urlunsplit(("http", parts.netloc, parts.path or "/", parts.query, ""))


def _is_https_location(location: str, public_url: str) -> bool:
    expected = urlsplit(public_url)
    actual = urlsplit(location)
    return actual.scheme == "https" and actual.netloc == expected.netloc


def verify_production_ssl(public_url: str, proxy_probe_url: str,
                          fetch: Callable[[str, Dict[str, str]], Tuple[int, str, Tuple[str, ...]]] = _http_probe) -> None:
    if urlsplit(public_url).scheme != "https" or not urlsplit(public_url).netloc:
        raise DemoError("production force_ssl requires an HTTPS public URL")
    if urlsplit(proxy_probe_url).scheme not in {"http", "https"} or not urlsplit(proxy_probe_url).netloc:
        raise DemoError("production force_ssl requires a proxy probe URL")

    status, location, _ = fetch(_https_redirect_target(public_url), {})
    if status not in {301, 302, 303, 307, 308} or not _is_https_location(location, public_url):
        raise DemoError("production HTTPS redirect evidence is missing")

    status, _, _ = fetch(public_url, {})
    if not 200 <= status < 400:
        raise DemoError("production HTTPS endpoint probe failed")

    status, location, _ = fetch(proxy_probe_url, {})
    if status not in {301, 302, 303, 307, 308} or not _is_https_location(location, public_url):
        raise DemoError("trusted forwarded-proto negative probe failed")

    status, location, cookies = fetch(proxy_probe_url, {"X-Forwarded-Proto": "https"})
    if not 200 <= status < 300 or location:
        raise DemoError("trusted forwarded-proto evidence is missing")
    if not any(any(attribute.strip() == "secure" for attribute in cookie.lower().split(";"))
               for cookie in cookies):
        raise DemoError("Secure cookie evidence is missing")


def ssl_policy(environment: str, public_url: str = "", proxy_probe_url: str = "",
               fetch: Callable[[str, Dict[str, str]], Tuple[int, str, Tuple[str, ...]]] = _http_probe) -> Dict[str, Any]:
    if environment in {"local", "demo", "ci"}:
        return {"force_ssl": "off", "ssl_disable_alert": 1}
    if environment != "production":
        raise DemoError("unknown SSL environment")
    verify_production_ssl(public_url, proxy_probe_url, fetch)
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


def _call(api: Api, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    try:
        return api.call(method, path, body)
    except DemoError:
        raise
    except (TimeoutError, OSError) as error:
        raise DemoError("platform API request failed") from error


def _get_resource(api: Api, resource_id: str, kind: str, name: str,
                  parent_id: Optional[str]) -> Optional[Dict[str, Any]]:
    status, payload = _call(api, "GET", "/v1/resources/{}".format(resource_id))
    if status == 404:
        return None
    if status != 200 or not isinstance(payload, dict):
        raise DemoError("get demo {} failed with HTTP {}".format(kind, status))
    if not _valid_resource(payload, resource_id, kind, name, parent_id):
        raise DemoError("persisted demo {} identity does not match".format(kind))
    return payload


def _find_resource(api: Api, kind: str, name: str, parent_id: Optional[str]) -> Optional[Dict[str, Any]]:
    parameters = {"query": name, "kind": kind, "archived": "active", "limit": "100"}
    if parent_id is not None:
        parameters["parentId"] = parent_id
    matches = []
    cursor = None
    while True:
        page_parameters = dict(parameters)
        if cursor is not None:
            page_parameters["cursor"] = cursor
        status, payload = _call(api, "GET", "/v1/resources?" + urlencode(page_parameters))
        if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise DemoError("list demo {} failed with HTTP {}".format(kind, status))
        for item in payload["items"]:
            if isinstance(item, dict) and item.get("kind") == kind and item.get("name") == name \
                    and item.get("parentId") == parent_id and isinstance(item.get("id"), str):
                matches.append(item)
        cursor = payload.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor:
            raise DemoError("list demo {} returned an invalid cursor".format(kind))
    if len(matches) > 1:
        raise DemoError("multiple demo {} resources match the fixed identity".format(kind))
    return matches[0] if matches else None


def _create_or_recover_resource(api: Api, kind: str, name: str, parent_id: Optional[str],
                                path: str, body: Dict[str, Any], label: str) -> Dict[str, Any]:
    existing = _find_resource(api, kind, name, parent_id)
    if existing is not None:
        return existing
    status, payload = _call(api, "POST", path, body)
    if status == 409:
        existing = _find_resource(api, kind, name, parent_id)
        if existing is not None:
            return existing
    created = _require(status, payload, 201, label)
    resource_id = created.get("id")
    if not isinstance(resource_id, str):
        raise DemoError("{} returned no resource id".format(label))
    return created


def _published_version(api: Api, survey_id: str, survey: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if survey.get("status") != "published" or survey.get("publishedVersion") != 1:
        return None
    status, versions = _call(api, "GET", "/v1/surveys/{}/versions".format(survey_id))
    if status != 200 or not isinstance(versions, list):
        raise DemoError("get demo survey versions failed with HTTP {}".format(status))
    return next(
        (item for item in versions if isinstance(item, dict) and item.get("version") == 1
         and item.get("live") is True and isinstance(item.get("engineSid"), int)),
        None,
    )


def ensure_demo_seed(operator: Api, owner: Api, metadata: Dict[str, Any],
                     engine_operations_url: str, metadata_path: Optional[Path] = None) -> Dict[str, Any]:
    tenant_id = metadata.get("tenantId")
    status, tenant = _call(operator, "GET", "/v1/platform/tenants/{}".format(tenant_id))
    if status != 200 or not isinstance(tenant, dict) or tenant.get("code") != TENANT_CODE \
            or tenant.get("status") != "active":
        raise DemoError("persisted demo tenant could not be validated")

    project_id = metadata.get("projectId")
    project = _get_resource(owner, project_id, "project", PROJECT_NAME, None) if project_id else None
    if project is None:
        project = _create_or_recover_resource(
            owner, "project", PROJECT_NAME, None, "/v1/projects", {"name": PROJECT_NAME}, "create demo project"
        )
        project_id = project.get("id")
        metadata.update({"projectId": project_id, "folderId": None, "surveyId": None})
        if metadata_path is not None:
            _write_metadata(metadata_path, metadata)

    folder_id = metadata.get("folderId")
    folder = _get_resource(owner, folder_id, "folder", FOLDER_NAME, project_id) if folder_id else None
    if folder is None:
        folder = _create_or_recover_resource(
            owner, "folder", FOLDER_NAME, project_id, "/v1/folders",
            {"parentId": project_id, "name": FOLDER_NAME}, "create demo folder",
        )
        folder_id = folder.get("id")
        metadata.update({"folderId": folder_id, "surveyId": None})
        if metadata_path is not None:
            _write_metadata(metadata_path, metadata)

    survey_id = metadata.get("surveyId")
    survey = None
    resource = _get_resource(owner, survey_id, "survey", SURVEY_NAME, folder_id) if survey_id else None
    if resource is None:
        resource = _create_or_recover_resource(
            owner, "survey", SURVEY_NAME, folder_id, "/v1/surveys",
            {"parentId": folder_id, "definition": demo_definition()}, "create demo survey",
        )
        survey_id = resource.get("id")
        metadata["surveyId"] = survey_id
        if metadata_path is not None:
            _write_metadata(metadata_path, metadata)
    status, candidate = _call(owner, "GET", "/v1/surveys/{}".format(survey_id))
    if status == 200 and isinstance(candidate, dict) and candidate.get("title") == SURVEY_NAME:
        survey = candidate
    elif status == 404:
        raise DemoError("demo survey disappeared after identity lookup")
    else:
        raise DemoError("get demo survey failed with HTTP {}".format(status))
    if survey is None:
        raise DemoError("demo survey identity does not match")

    version = _published_version(owner, survey_id, survey)
    if version is None:
        outcome = _require(
            *_call(owner, "POST", "/v1/surveys/{}/publish".format(survey_id)),
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


def _persist(metadata_path: Path, metadata: Dict[str, Any], **changes: Any) -> None:
    metadata.update(changes)
    _write_metadata(metadata_path, metadata)


def _get_optional_object(api: Api, path: str, label: str) -> Optional[Dict[str, Any]]:
    status, payload = _call(api, "GET", path)
    if status == 404:
        return None
    if status != 200 or not isinstance(payload, dict):
        raise DemoError("{} failed with HTTP {}".format(label, status))
    return payload


def _recover_tenant(operator: Api, metadata: Dict[str, Any], metadata_path: Path) -> Dict[str, Any]:
    tenant = None
    tenant_id = metadata.get("tenantId")
    if isinstance(tenant_id, str):
        tenant = _get_optional_object(
            operator, "/v1/platform/tenants/{}".format(tenant_id), "get persisted demo tenant"
        )
        if tenant is not None and tenant.get("code") != TENANT_CODE:
            raise DemoError("persisted demo tenant identity does not match")
    if tenant is None:
        tenant = _get_optional_object(
            operator, "/v1/platform/tenants?" + urlencode({"code": TENANT_CODE}), "find demo tenant"
        )
    if tenant is None:
        status, payload = _call(
            operator, "POST", "/v1/platform/tenants", {"code": TENANT_CODE, "name": TENANT_NAME}
        )
        if status == 409:
            tenant = _get_optional_object(
                operator, "/v1/platform/tenants?" + urlencode({"code": TENANT_CODE}), "recover demo tenant"
            )
            if tenant is None:
                raise DemoError("create demo tenant conflicted without a recoverable tenant")
        else:
            tenant = _require(status, payload, 201, "create demo tenant")
    tenant_id = tenant.get("id")
    if not isinstance(tenant_id, str) or tenant.get("code") != TENANT_CODE:
        raise DemoError("demo tenant response is invalid")
    _persist(metadata_path, metadata, tenantId=tenant_id)
    return tenant


def _recover_plan(operator: Api, metadata: Dict[str, Any], metadata_path: Path) -> Dict[str, Any]:
    plan = _get_optional_object(
        operator, "/v1/platform/plans/{}".format(quote(PLAN_CODE, safe="")), "find demo plan"
    )
    if plan is None:
        status, payload = _call(operator, "POST", "/v1/platform/plans", {
            "planCode": PLAN_CODE,
            "capabilities": ["survey.read", "survey.write", "response.collect"],
            "quotas": {"member.seats": 5, "response.valid_completed": 1000},
            "exportWindowDays": 30,
        })
        if status == 409:
            plan = _get_optional_object(
                operator, "/v1/platform/plans/{}".format(quote(PLAN_CODE, safe="")), "recover demo plan"
            )
            if plan is None:
                raise DemoError("create demo plan conflicted without a recoverable plan")
        else:
            plan = _require(status, payload, 201, "create demo plan")
    if plan.get("planCode") != PLAN_CODE or not isinstance(plan.get("id"), str):
        raise DemoError("demo plan response is invalid")
    _persist(metadata_path, metadata, planVersionId=plan["id"])
    return plan


def _recover_engine(operator: Api, tenant_id: str, engine_internal_url: str,
                    metadata: Dict[str, Any], metadata_path: Path) -> None:
    path = "/v1/platform/tenants/{}/engine-instances".format(tenant_id)
    status, instances = _call(operator, "GET", path)
    if status != 200 or not isinstance(instances, list):
        raise DemoError("list demo engines failed with HTTP {}".format(status))
    matches = [item for item in instances if isinstance(item, dict) and item.get("id") == ENGINE_INSTANCE]
    if len(matches) > 1:
        raise DemoError("multiple demo engines match the fixed identity")
    if not matches:
        status, payload = _call(operator, "POST", path, {"id": ENGINE_INSTANCE, "baseUrl": engine_internal_url})
        if status == 409:
            status, instances = _call(operator, "GET", path)
            matches = [item for item in instances if isinstance(item, dict) and item.get("id") == ENGINE_INSTANCE] \
                if status == 200 and isinstance(instances, list) else []
            if len(matches) != 1:
                raise DemoError("register demo engine conflicted without a recoverable engine")
        else:
            matches = [_require(status, payload, 201, "register demo engine")]
    if matches[0].get("tenantId") != tenant_id or matches[0].get("baseUrl") != engine_internal_url:
        raise DemoError("demo engine identity does not match")
    _persist(metadata_path, metadata, engineInstanceId=ENGINE_INSTANCE)


def _bootstrap(operator: Api, engine_internal_url: str, metadata_path: Path,
               event_secret_path: Path) -> Dict[str, Any]:
    metadata = _load_object(metadata_path) if metadata_path.exists() else {
        "schemaVersion": 2, "tenantId": None, "planVersionId": None, "onboarded": False,
        "activated": False, "projectId": None, "folderId": None, "surveyId": None,
        "engineInstanceId": None,
    }
    tenant = _recover_tenant(operator, metadata, metadata_path)
    tenant_id = tenant["id"]
    status = tenant.get("status")
    if status == "provisioning":
        plan = _recover_plan(operator, metadata, metadata_path)
        onboard_status, onboarded = _call(
            operator, "POST", "/v1/platform/tenants/{}/onboarding".format(tenant_id), {
                "planVersionId": plan["id"], "kind": "TRIAL", "days": 3650, "ownerActorId": OWNER_ACTOR,
            },
        )
        if onboard_status == 409 and isinstance(onboarded, dict) and onboarded.get("error") == "already_onboarded":
            pass
        else:
            _require(onboard_status, onboarded, 200, "onboard demo tenant")
        _persist(metadata_path, metadata, onboarded=True)
        activated = _require(
            *_call(operator, "POST", "/v1/platform/tenants/{}/status".format(tenant_id), {"status": "active"}),
            200, "activate demo tenant",
        )
        if activated.get("status") != "active":
            raise DemoError("activate demo tenant returned an invalid status")
        _persist(metadata_path, metadata, activated=True)
    elif status == "active":
        _persist(metadata_path, metadata, onboarded=True, activated=True)
    else:
        raise DemoError("demo tenant is not usable")

    _recover_engine(operator, tenant_id, engine_internal_url, metadata, metadata_path)
    if not event_secret_path.exists():
        issued = _require(
            *_call(operator, "POST", "/v1/platform/engine-instances/{}/event-secret".format(ENGINE_INSTANCE)),
            200, "issue demo engine event secret",
        )
        secret = issued.get("secret")
        if not isinstance(secret, str) or len(secret) != 64:
            raise DemoError("engine event secret response is invalid")
        write_private(event_secret_path, secret)
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


def cmd_check_production_ssl(args: argparse.Namespace) -> None:
    ssl_policy("production", args.public_url, args.proxy_probe_url)
    print("production SSL ready")


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
    ssl_check = commands.add_parser("check-production-ssl")
    ssl_check.add_argument("--public-url", required=True)
    ssl_check.add_argument("--proxy-probe-url", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "start":
            cmd_start(args)
        elif args.command == "refresh-token":
            cmd_refresh_token(args)
        elif args.command == "check-production-ssl":
            cmd_check_production_ssl(args)
    except DemoError as error:
        print("demo lifecycle failed: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
