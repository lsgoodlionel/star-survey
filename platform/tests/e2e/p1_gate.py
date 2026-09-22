#!/usr/bin/env python3

"""P1 闸门端到端验证的宿主机驱动（由 platform/deploy/test/run-p1-e2e.sh 编排，不单独运行）。

只走平台的公开 HTTP 接口，令牌用与平台相同的 HMAC 密钥现签（HS256，租户只在 ``tenant_id`` 声明里）。
每一步都断言；任何一步不符立即以非零退出码结束，后面的步骤不再执行。

子命令：

* ``operator``：运营开通租户、发布含 ``member.seats`` 的套餐、开通订阅与所有者、启用租户、
  登记引擎实例，并签发该实例的事件密钥（只写入权限 0600 的文件，从不打印）；
* ``owner-publish``：所有者建问卷、存草稿、发布，并核对状态、已发布版本与公开路由；
* ``republish``：再次发布同一问卷必须 409 already_published。

密钥只从环境变量 ``PLATFORM_JWT_HMAC_SECRET`` 读取。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

TOKEN_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 300  # 发布要等网关跑完七个阶段
OPERATOR_ACTOR = "p1-e2e-operator"
OWNER_ACTOR = "p1-e2e-owner"
PLAN_SEATS = 5
OPERATOR_ROLE = "platform_operator"


class StepFailed(Exception):
    """某个断言不成立：驱动立即停止。"""


def expect(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print("  [ok] " + label, file=sys.stderr)
        return
    print("  [FAIL] " + label + (": " + detail if detail else ""), file=sys.stderr)
    raise StepFailed(label)


# ------------------------------------------------------------------ tokens


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_token(secret: str, subject: str, tenant_id: str, roles: List[str]) -> str:
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    claims = {"sub": subject, "tenant_id": tenant_id, "roles": roles, "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = (header + "." + payload).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return header + "." + payload + "." + _b64url(signature)


# -------------------------------------------------------------------- http


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
        return raw.decode("utf-8", "replace")[:500]


def _brief(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)[:600]


# ------------------------------------------------------------------- state


def load_state(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def write_private(path: Path, content: str) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def jwt_secret() -> str:
    secret = os.environ.get("PLATFORM_JWT_HMAC_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise SystemExit("PLATFORM_JWT_HMAC_SECRET must be set (at least 32 bytes)")
    return secret


def owner_api(args: argparse.Namespace, state: Dict[str, Any]) -> Api:
    return Api(args.base_url, mint_token(jwt_secret(), state["ownerActor"], state["tenantId"], []))


# ---------------------------------------------------------------- commands


def cmd_operator(args: argparse.Namespace) -> None:
    operator = Api(args.base_url, mint_token(jwt_secret(), OPERATOR_ACTOR, str(uuid.uuid4()), [OPERATOR_ROLE]))
    code = "p1-e2e-" + uuid.uuid4().hex[:8]

    status, tenant = operator.call("POST", "/v1/platform/tenants", {"code": code, "name": "P1 e2e tenant"})
    expect(status == 201 and isinstance(tenant, dict) and tenant.get("status") == "provisioning",
           "operator creates tenant -> 201 provisioning", "{} {}".format(status, _brief(tenant)))
    tenant_id = tenant["id"]

    plan_body = {"planCode": "p1-e2e-" + uuid.uuid4().hex[:8],
                 "capabilities": ["survey.read", "survey.write", "response.collect"],
                 "quotas": {"member.seats": PLAN_SEATS, "response.valid_completed": 1000},
                 "exportWindowDays": 30}
    status, plan = operator.call("POST", "/v1/platform/plans", plan_body)
    expect(status == 201 and isinstance(plan, dict) and plan.get("version") == 1,
           "operator publishes plan with member.seats={} -> 201 v1".format(PLAN_SEATS),
           "{} {}".format(status, _brief(plan)))

    status, onboarded = operator.call("POST", "/v1/platform/tenants/{}/onboarding".format(tenant_id),
                                      {"planVersionId": plan["id"], "kind": "TRIAL", "days": 14,
                                       "ownerActorId": OWNER_ACTOR})
    expect(status == 200 and isinstance(onboarded, dict) and onboarded.get("ownerActorId") == OWNER_ACTOR,
           "operator onboards tenant with owner -> 200", "{} {}".format(status, _brief(onboarded)))

    status, activated = operator.call("POST", "/v1/platform/tenants/{}/status".format(tenant_id),
                                      {"status": "active"})
    expect(status == 200 and isinstance(activated, dict) and activated.get("status") == "active",
           "operator activates tenant -> 200 active", "{} {}".format(status, _brief(activated)))

    status, instance = operator.call("POST", "/v1/platform/tenants/{}/engine-instances".format(tenant_id),
                                     {"id": args.instance, "baseUrl": args.engine_base_url})
    expect(status == 201 and isinstance(instance, dict) and instance.get("id") == args.instance,
           "operator registers engine instance {} -> 201".format(args.instance),
           "{} {}".format(status, _brief(instance)))

    status, issued = operator.call("POST", "/v1/platform/engine-instances/{}/event-secret".format(args.instance))
    secret = issued.get("secret") if isinstance(issued, dict) else None
    expect(status == 200 and isinstance(secret, str) and len(secret) == 64
           and issued.get("engineInstanceId") == args.instance,
           "operator issues the per-instance event secret -> 200 (value withheld)", "http {}".format(status))
    write_private(Path(args.secret_file), secret)

    save_state(Path(args.state), {"tenantId": tenant_id, "ownerActor": OWNER_ACTOR, "instance": args.instance})


def cmd_owner_publish(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    state = load_state(state_path)
    owner = owner_api(args, state)
    with open(args.definition, encoding="utf-8") as handle:
        definition = json.load(handle)

    placeholder = dict(definition, title="P1 e2e placeholder")
    status, created = owner.call("POST", "/v1/surveys", {"parentId": args.project, "definition": placeholder})
    expect(status == 201 and isinstance(created, dict) and created.get("status") == "draft",
           "owner creates survey under project -> 201 draft", "{} {}".format(status, _brief(created)))
    survey_id = created["id"]

    status, draft = owner.call("PUT", "/v1/surveys/{}/draft".format(survey_id),
                               {"expectedVersion": created["draftVersion"], "definition": definition})
    expect(status == 200 and isinstance(draft, dict) and draft.get("version") == created["draftVersion"] + 1,
           "owner saves the fixture as draft -> 200 next version", "{} {}".format(status, _brief(draft)))

    status, outcome = owner.call("POST", "/v1/surveys/{}/publish".format(survey_id))
    survey = (outcome or {}).get("survey") if isinstance(outcome, dict) else None
    expect(status == 200 and isinstance(survey, dict) and survey.get("status") == "published",
           "owner publishes -> 200 published", "{} {}".format(status, _brief(outcome)))
    version = outcome.get("version") or {}
    sid = version.get("engineSid")
    expect(version.get("version") == 1 and version.get("engineInstanceId") == state["instance"]
           and isinstance(sid, int) and sid > 0,
           "publish outcome carries version 1 bound to {}".format(state["instance"]), _brief(version))

    status, current = owner.call("GET", "/v1/surveys/{}".format(survey_id))
    expect(status == 200 and current.get("status") == "published" and current.get("publishedVersion") == 1,
           "GET survey -> published, publishedVersion 1", "{} {}".format(status, _brief(current)))

    status, versions = owner.call("GET", "/v1/surveys/{}/versions".format(survey_id))
    first = versions[0] if isinstance(versions, list) and len(versions) == 1 else {}
    expect(status == 200 and first.get("version") == 1 and first.get("engineInstanceId") == state["instance"]
           and first.get("engineSid") == sid and str(first.get("fingerprint", "")).startswith("fm1:")
           and len(first.get("fields") or []) > 0,
           "GET versions -> exactly v1 with binding (instance, sid {}, fingerprint, fields)".format(sid),
           "{} {}".format(status, _brief(versions)))

    status, route = owner.call("GET", "/v1/survey-routes/{}".format(survey_id))
    expect(status == 200 and route.get("publicId") == survey_id and route.get("engineInstanceId") == state["instance"]
           and route.get("engineSid") == sid and route.get("tenantId") == state["tenantId"],
           "public route {} -> {} sid {}".format(survey_id, state["instance"], sid),
           "{} {}".format(status, _brief(route)))

    status, reverse = owner.call("GET", "/v1/survey-routes?engineInstanceId={}&engineSid={}".format(
        state["instance"], sid))
    expect(status == 200 and reverse.get("publicId") == survey_id,
           "reverse route ({}, {}) -> survey public id".format(state["instance"], sid),
           "{} {}".format(status, _brief(reverse)))

    save_state(state_path, dict(state, surveyId=survey_id, engineSid=sid))


def cmd_republish(args: argparse.Namespace) -> None:
    state = load_state(Path(args.state))
    status, body = owner_api(args, state).call("POST", "/v1/surveys/{}/publish".format(state["surveyId"]))
    expect(status == 409 and isinstance(body, dict) and body.get("error") == "already_published",
           "re-publish -> 409 already_published", "{} {}".format(status, _brief(body)))


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="platform base URL reachable from this host")
    parser.add_argument("--state", required=True, help="JSON file carrying ids between steps")
    commands = parser.add_subparsers(dest="command", required=True)

    operator = commands.add_parser("operator")
    operator.add_argument("--instance", required=True)
    operator.add_argument("--engine-base-url", required=True)
    operator.add_argument("--secret-file", required=True, help="written with mode 0600; never printed")
    operator.set_defaults(handler=cmd_operator)

    publish = commands.add_parser("owner-publish")
    publish.add_argument("--project", required=True)
    publish.add_argument("--definition", required=True)
    publish.set_defaults(handler=cmd_owner_publish)

    republish = commands.add_parser("republish")
    republish.set_defaults(handler=cmd_republish)
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
