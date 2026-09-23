#!/usr/bin/env python3

"""P1 闸门端到端验证的宿主机驱动（由 platform/deploy/test/run-p1-e2e.sh 编排，不单独运行）。

只走平台的公开 HTTP 接口，令牌用与平台相同的 HMAC 密钥现签（HS256，租户只在 ``tenant_id`` 声明里）。
每一步都断言；任何一步不符立即以非零退出码结束，后面的步骤不再执行。

子命令：

* ``operator``：运营开通租户、发布含 ``member.seats`` 的套餐、开通订阅与所有者、启用租户、
  登记引擎实例，并签发该实例的事件密钥（只写入权限 0600 的文件，从不打印）；
* ``owner-project``：所有者经资源树接口建项目（创建者获得项目授权），并在可见列表里找到它；
* ``owner-publish``：所有者在该项目下建问卷、存草稿、发布，并核对状态、已发布版本与公开路由；
* ``republish``：草稿未改动时再次发布同一问卷必须 409 already_published；
* ``republish-changed``：改稿后重新发布（ADR 0012）→ 第 2 版、新 sid、公开路由切到新 sid、旧 sid 反查仍归属
  本问卷、第 1 版标记为已被取代且引擎已收口；随后对第 2 版做一次漂移检查必须 match。
* ``restore-v1``：旧版恢复（ADR 0012 决定 7）→ 把第 1 版恢复为草稿，恢复本身不得改动在线版本与公开路由；
  再发布一次得到第 3 版（又一个新 sid），第 1、2 版仍可查、仍能反查，路由切到第 3 版。

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


def cmd_owner_project(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    state = load_state(state_path)
    owner = owner_api(args, state)

    status, project = owner.call("POST", "/v1/projects", {"name": "P1 e2e project"})
    expect(status == 201 and isinstance(project, dict) and project.get("kind") == "project"
           and project.get("parentId") is None and project.get("name") == "P1 e2e project",
           "owner creates project via POST /v1/projects -> 201", "{} {}".format(status, _brief(project)))
    project_id = project["id"]

    status, fetched = owner.call("GET", "/v1/resources/{}".format(project_id))
    expect(status == 200 and isinstance(fetched, dict) and fetched.get("id") == project_id,
           "GET /v1/resources/{id} -> 200 the project", "{} {}".format(status, _brief(fetched)))

    status, page = owner.call("GET", "/v1/resources")
    items = page.get("items") if isinstance(page, dict) else None
    expect(status == 200 and isinstance(items, list) and [i.get("id") for i in items] == [project_id],
           "GET /v1/resources -> exactly the new project", "{} {}".format(status, _brief(page)))

    save_state(state_path, dict(state, projectId=project_id))


def cmd_owner_publish(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    state = load_state(state_path)
    owner = owner_api(args, state)
    with open(args.definition, encoding="utf-8") as handle:
        definition = json.load(handle)

    placeholder = dict(definition, title="P1 e2e placeholder")
    status, created = owner.call("POST", "/v1/surveys", {"parentId": state["projectId"], "definition": placeholder})
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


def cmd_republish_changed(args: argparse.Namespace) -> None:
    state_path = Path(args.state)
    state = load_state(state_path)
    owner = owner_api(args, state)
    survey_id, old_sid = state["surveyId"], state["engineSid"]
    with open(args.definition, encoding="utf-8") as handle:
        definition = dict(json.load(handle), title="P1 e2e second version")

    status, draft = owner.call("GET", "/v1/surveys/{}/draft".format(survey_id))
    expect(status == 200, "GET draft -> 200", "{} {}".format(status, _brief(draft)))
    status, saved = owner.call("PUT", "/v1/surveys/{}/draft".format(survey_id),
                               {"expectedVersion": draft["version"], "definition": definition})
    expect(status == 200 and saved.get("version") == draft["version"] + 1,
           "owner saves a changed draft of the published survey -> 200", "{} {}".format(status, _brief(saved)))

    status, outcome = owner.call("POST", "/v1/surveys/{}/publish".format(survey_id))
    version = (outcome or {}).get("version") if isinstance(outcome, dict) else None
    version = version or {}
    new_sid = version.get("engineSid")
    expect(status == 200 and version.get("version") == 2 and version.get("live") is True,
           "republish -> 200 version 2 (live)", "{} {}".format(status, _brief(outcome)))
    expect(isinstance(new_sid, int) and new_sid != old_sid,
           "version 2 is a new engine survey (sid {} -> {})".format(old_sid, new_sid), _brief(version))

    status, versions = owner.call("GET", "/v1/surveys/{}/versions".format(survey_id))
    first = versions[0] if isinstance(versions, list) and len(versions) == 2 else {}
    expect(status == 200 and first.get("engineSid") == old_sid and first.get("live") is False
           and first.get("supersededAt") and first.get("engineClosedAt"),
           "version 1 is kept, superseded, and closed in the engine", "{} {}".format(status, _brief(versions)))

    status, route = owner.call("GET", "/v1/survey-routes/{}".format(survey_id))
    expect(status == 200 and route.get("engineSid") == new_sid,
           "public route {} now -> sid {}".format(survey_id, new_sid), "{} {}".format(status, _brief(route)))
    status, reverse = owner.call("GET", "/v1/survey-routes?engineInstanceId={}&engineSid={}".format(
        state["instance"], old_sid))
    expect(status == 200 and reverse.get("publicId") == survey_id,
           "old sid {} still resolves to the survey".format(old_sid), "{} {}".format(status, _brief(reverse)))

    status, check = owner.call("POST", "/v1/surveys/{}/versions/2/drift-checks".format(survey_id))
    expect(status == 200 and check.get("outcome") == "match" and check.get("engineSid") == new_sid,
           "drift check of version 2 against the real engine -> match", "{} {}".format(status, _brief(check)))

    save_state(state_path, dict(state, oldSid=old_sid, engineSid=new_sid))


def cmd_restore_v1(args: argparse.Namespace) -> None:
    """旧版恢复：恢复只写草稿，在线版本与公开路由必须原地不动；随后发布才产生新的一版。"""
    state_path = Path(args.state)
    state = load_state(state_path)
    owner = owner_api(args, state)
    survey_id, live_sid = state["surveyId"], state["engineSid"]

    status, draft = owner.call("GET", "/v1/surveys/{}/draft".format(survey_id))
    expect(status == 200, "GET draft -> 200", "{} {}".format(status, _brief(draft)))
    status, restored = owner.call("POST", "/v1/surveys/{}/versions/1/restore".format(survey_id),
                                  {"expectedVersion": draft["version"]})
    expect(status == 200 and restored.get("version") == draft["version"] + 1,
           "restore version 1 into the draft -> 200", "{} {}".format(status, _brief(restored)))

    status, survey = owner.call("GET", "/v1/surveys/{}".format(survey_id))
    expect(status == 200 and survey.get("publishedVersion") == 2,
           "restore left the live version at 2", "{} {}".format(status, _brief(survey)))
    status, route = owner.call("GET", "/v1/survey-routes/{}".format(survey_id))
    expect(status == 200 and route.get("engineSid") == live_sid,
           "restore left the public route on sid {}".format(live_sid), "{} {}".format(status, _brief(route)))
    status, versions = owner.call("GET", "/v1/surveys/{}/versions".format(survey_id))
    expect(status == 200 and isinstance(versions, list) and len(versions) == 2
           and versions[1].get("live") is True,
           "restore added no version and version 2 is still live", "{} {}".format(status, _brief(versions)))

    status, outcome = owner.call("POST", "/v1/surveys/{}/publish".format(survey_id))
    version = ((outcome or {}).get("version") if isinstance(outcome, dict) else None) or {}
    third_sid = version.get("engineSid")
    expect(status == 200 and version.get("version") == 3 and version.get("live") is True,
           "publishing the restored draft -> 200 version 3 (live)", "{} {}".format(status, _brief(outcome)))
    expect(isinstance(third_sid, int) and third_sid not in (live_sid, state["oldSid"]),
           "version 3 is yet another engine survey (sid {})".format(third_sid), _brief(version))

    status, route = owner.call("GET", "/v1/survey-routes/{}".format(survey_id))
    expect(status == 200 and route.get("engineSid") == third_sid,
           "public route now -> sid {}".format(third_sid), "{} {}".format(status, _brief(route)))
    for label, sid in (("version 1", state["oldSid"]), ("version 2", live_sid)):
        status, reverse = owner.call("GET", "/v1/survey-routes?engineInstanceId={}&engineSid={}".format(
            state["instance"], sid))
        expect(status == 200 and reverse.get("publicId") == survey_id,
               "{} sid {} still resolves to the survey".format(label, sid), "{} {}".format(status, _brief(reverse)))

    save_state(state_path, dict(state, supersededSid=live_sid, engineSid=third_sid))


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

    project = commands.add_parser("owner-project")
    project.set_defaults(handler=cmd_owner_project)

    publish = commands.add_parser("owner-publish")
    publish.add_argument("--definition", required=True)
    publish.set_defaults(handler=cmd_owner_publish)

    republish = commands.add_parser("republish")
    republish.set_defaults(handler=cmd_republish)

    changed = commands.add_parser("republish-changed")
    changed.add_argument("--definition", required=True)
    changed.set_defaults(handler=cmd_republish_changed)

    restore = commands.add_parser("restore-v1")
    restore.set_defaults(handler=cmd_restore_v1)
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
