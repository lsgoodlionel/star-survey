#!/usr/bin/env python3

"""RemoteControl 写答卷接口的旁路与闸门，在真引擎上验证（见 run-rpc-gate.sh）。

**这是 P0 发现 12、21 的复现脚本。** 要证明的事实一句话：一份访问窗口**已经截止**的问卷，
正常作答者被 `beforeSurveyPage` 拒在门外、连答案都写不进库；而同一时刻用 RemoteControl 的
`add_response` 就能把一份带 `submitdate` 的答卷直接塞进答卷表——访问规则、按身份限次、
服务端考试计时、题型的服务端校验对这条路径全部失效（ADR 0007「守住 API 面」）。

场景：

A  正常路径的对照：窗口已截止 → 真实 HTTP 作答被拒，答卷表里 0 份已提交。
B  **旁路**：raw JSON-RPC `add_response` 打同一个 sid。
     修复前：返回答卷号，库里多出一份 submitdate 非空的答卷（红，漏洞成立）。
     修复后：HTTP 403 ＋ JSON-RPC error，库里仍然 0 份（绿）。
C  **旁路 2**：`update_response` 改写一份已有答卷（P0 发现 21 的 `encryptSave()`）。
     修复前：答案被改掉；修复后：被拒、答案原样。
D  运维开关：打开 MJY_ALLOW_RPC_RESPONSE_WRITES 后 `add_response` 放行
     （"运维/管理员"与"作答者提交"是两类语义，前者要显式打开，且留日志）。
E  **不破坏发布网关现有功能**：闸门装着的同时，把网关真正用到的每条能力都跑一遍——
     发布七阶段（含参与者与邀请码回读）、回读核对（get_fieldmap / list_questions /
     get_survey_properties）、收口（set_survey_properties 设过期）、答卷导出
     （export_responses）、插件通道（newDirectRequest 的 policyStatus）。

B、C 两条**刻意不走** `pubgw.rpc.RemoteControlClient`：网关侧的白名单（ADR 0021）已经
让客户端发不出这两个方法，而这里要探的是**引擎端点**本身。所以请求体是手写的。
"""

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from access_policy import (  # noqa: E402
    Context,
    definition,
    docker_probe,
    local,
    publish_ok,
    start_url,
    texts,
)
from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from pubgw.close import close_survey  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402

#: 旁路塞进去的答案值；库里出现它就等于旁路成功。
SMUGGLED = "SMUGGLED-BY-RPC"
#: 直接写进库的原始答案值；`update_response` 改掉它就等于旁路成功。
ORIGINAL = "ORIGINAL-ANSWER"
OVERRIDE_ENV = "MJY_ALLOW_RPC_RESPONSE_WRITES"


def quoted(driver: str, column: str) -> str:
    """答卷表的列名里有 ``#`` 之类的字符，两种数据库的引号不同。"""
    return '"{}"'.format(column) if driver == "pgsql" else "`{}`".format(column)


# ------------------------------------------------------------------ raw RPC


def raw_rpc(container: str, method: str, params: List[Any]) -> Dict[str, Any]:
    """手写 JSON-RPC 请求直接打引擎端点，绕过网关客户端的白名单。

    返回 ``{"http": <状态码>, "body": <原始正文>, "decoded": <解析结果或 None>}``。
    """
    payload = json.dumps({"method": method, "params": params, "id": 7}).encode("utf-8")
    command = [
        "docker", "exec", "-i", container,
        "curl", "-s", "--max-time", "60", "-o", "/dev/stdout",
        "-w", "\n<<HTTP:%{http_code}>>",
        "-H", "Content-Type: application/json",
        "--data-binary", "@-", "http://localhost/index.php/admin/remotecontrol",
    ]
    completed = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise RuntimeError("docker exec curl failed: " + completed.stderr.decode("utf-8", "replace"))
    text = completed.stdout.decode("utf-8", "replace")
    body, _, marker = text.rpartition("\n<<HTTP:")
    status = marker.rstrip(">>\n") or "0"
    try:
        decoded = json.loads(body)
    except ValueError:
        decoded = None
    return {"http": status, "body": body, "decoded": decoded}


def session_key(container: str) -> str:
    client = RemoteControlClient(DockerCurlTransport(container))
    return client.login(ADMIN_USER, ADMIN_PASSWORD)


# ------------------------------------------------------------------ 环境


def set_override(container: str, is_enabled: bool) -> None:
    """按实例打开/关掉运维开关。

    Apache 的 SetEnv 才能让 PHP 的 getenv 看到，所以写一段 conf 再 graceful。
    """
    conf = "/etc/apache2/conf-enabled/zz-rpc-override.conf"
    if is_enabled:
        body = 'SetEnv {} 1\n'.format(OVERRIDE_ENV)
        subprocess.run(["docker", "exec", "-i", container, "tee", conf],
                       input=body, capture_output=True, text=True, check=True)
    else:
        subprocess.run(["docker", "exec", container, "rm", "-f", conf], check=True)
    subprocess.run(["docker", "exec", container, "apachectl", "-k", "graceful"],
                   capture_output=True, check=True)


def closed_window_survey(context: Context, title: str, now: datetime, allow_edit: bool = False) -> int:
    """一份访问窗口已经截止的问卷：正常作答必被 beforeSurveyPage 拒。

    ``allow_edit`` 打开引擎的 ``alloweditaftercompletion``——``update_response``
    本身要求它为 Y（`remotecontrol_handle.php:3489`），所以探那条旁路必须用这种问卷。
    平台侧确实有"允许改已提交答卷"的问卷，这不是为了测试造的特例。
    """
    closes = now - timedelta(hours=1)
    policy = {"policyVersion": 1,
              "window": {"closesAt": local("Asia/Shanghai", closes), "timezone": "Asia/Shanghai"}}
    payload = definition(title, policy)
    if allow_edit:
        payload["settings"]["alloweditaftercompletion"] = "Y"
    return publish_ok(context, payload)


def answer_column(context: Context, survey_id: int) -> str:
    """QNOTE 这道题在答卷表里的列名。

    列名由引擎生成、由题型主题决定，猜不得：直接问 ``get_fieldmap``——平台自己
    也是这么拿列名的（ADR 0013）。
    """
    client = RemoteControlClient(DockerCurlTransport(context.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        fieldmap = client.get_fieldmap(survey_id)
    finally:
        client.logout()
    for name, entry in fieldmap.items():
        if isinstance(entry, dict) and entry.get("title") == "QNOTE":
            return name
    raise RuntimeError("QNOTE not found in the fieldmap of sid {}: {}".format(survey_id, sorted(fieldmap)))


# ------------------------------------------------------------------ 场景


def insert_response(context: Context, survey_id: int, column: str, value: str) -> None:
    """直接往答卷表写一份未提交的答卷（datestamp 问卷的 startdate 是必填列）。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    context.db.rows(
        "INSERT INTO lime_responses_{} (startlanguage, startdate, datestamp, {}) "
        "VALUES ('en', '{}', '{}', '{}')".format(
            survey_id, quoted(context.driver, column), now, now, value))


def scenario_normal_path_is_denied(context: Context, survey_id: int) -> None:
    print("A: 正常路径对照——窗口已截止，真实作答被拒", file=sys.stderr)
    pages = context.respond(context.new_jar(), [{"get": start_url(survey_id)}])
    context.check("A: 作答者被拒", pages[0]["kind"] == "message" and "截止" in pages[0]["text"], texts(pages))
    context.check("A: 答卷表里没有已提交的答卷", context.submitted(survey_id) == 0)


def scenario_add_response_bypass(context: Context, survey_id: int) -> None:
    print("B: 旁路——RemoteControl add_response 打同一个 sid", file=sys.stderr)
    column = answer_column(context, survey_id)
    answer = raw_rpc(context.container, "add_response", [
        session_key(context.container), survey_id,
        {column: SMUGGLED, "submitdate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
    ])
    submitted = context.submitted(survey_id)
    smuggled = int(context.db.value(
        "SELECT COUNT(*) FROM lime_responses_{} WHERE {} = '{}'".format(
            survey_id, quoted(context.driver, column), SMUGGLED)))
    context.check("B: add_response 被拒（HTTP 403）", answer["http"] == "403", answer)
    context.check("B: 应答是 JSON-RPC error",
                  isinstance(answer["decoded"], dict) and answer["decoded"].get("error"), answer["body"][:300])
    context.check("B: 答卷表里仍然没有已提交的答卷（旁路被堵）", submitted == 0, submitted)
    context.check("B: 偷塞的答案一个字都没进库", smuggled == 0, smuggled)


def scenario_update_response_bypass(context: Context, now: datetime) -> None:
    print("C: 旁路 2——update_response 改写已有答卷（绕过 EM 与全部闸门）", file=sys.stderr)
    survey_id = closed_window_survey(context, "RPC update bypass", now, allow_edit=True)
    column = answer_column(context, survey_id)
    insert_response(context, survey_id, column, ORIGINAL)
    response_id = context.db.value(
        "SELECT id FROM lime_responses_{} WHERE {} = '{}'".format(
            survey_id, quoted(context.driver, column), ORIGINAL))
    answer = raw_rpc(context.container, "update_response", [
        session_key(context.container), survey_id, {"id": int(response_id), column: SMUGGLED},
    ])
    stored = context.db.value("SELECT {} FROM lime_responses_{} WHERE id = {}".format(
        quoted(context.driver, column), survey_id, int(response_id)))
    context.check("C: update_response 被拒（HTTP 403）", answer["http"] == "403", answer)
    context.check("C: 库里的答案原样未动（改写被堵）", stored == ORIGINAL, stored)


def scenario_operator_override(context: Context, now: datetime) -> None:
    print("D: 运维开关——显式打开后 add_response 放行", file=sys.stderr)
    survey_id = closed_window_survey(context, "RPC override", now)
    column = answer_column(context, survey_id)
    set_override(context.container, True)
    try:
        answer = raw_rpc(context.container, "add_response", [
            session_key(context.container), survey_id,
            {column: SMUGGLED, "submitdate": now.strftime("%Y-%m-%d %H:%M:%S")},
        ])
        written = int(context.db.value(
            "SELECT COUNT(*) FROM lime_responses_{} WHERE {} = '{}'".format(
                survey_id, quoted(context.driver, column), SMUGGLED)))
    finally:
        set_override(context.container, False)
    context.check("D: 开关打开时 add_response 不再被拒", answer["http"] == "200", answer)
    context.check("D: 这一份确实写进去了（运维语义）", written == 1, written)

    blocked = raw_rpc(context.container, "add_response", [
        session_key(context.container), survey_id,
        {column: SMUGGLED + "-AGAIN", "submitdate": now.strftime("%Y-%m-%d %H:%M:%S")},
    ])
    context.check("D: 关掉开关之后立刻恢复拒绝", blocked["http"] == "403", blocked)


def scenario_gateway_still_works(context: Context, now: datetime) -> None:
    """闸门装着的同时，网关真正用到的每条能力都必须照旧。"""
    print("E: 闸门不破坏发布网关的既有功能", file=sys.stderr)
    payload = definition("RPC gate regression",
                         {"policyVersion": 1, "limits": {"maxDurationSeconds": 3600}},
                         participants=[{"ref": "a"}, {"ref": "b"}])
    survey_id = publish_ok(context, payload)
    context.check("E: 发布七阶段走通（含参与者）", survey_id > 0, survey_id)
    context.check("E: 邀请码按 ref 回读到了",
                  len(context.invitations.get(survey_id) or []) == 2, context.invitations.get(survey_id))

    client = RemoteControlClient(DockerCurlTransport(context.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        fieldmap = client.get_fieldmap(survey_id)
        questions = client.list_questions(survey_id)
        properties = client.get_survey_properties(survey_id)
        context.check("E: get_fieldmap 照旧", isinstance(fieldmap, dict) and bool(fieldmap))
        context.check("E: list_questions 照旧", isinstance(questions, list) and bool(questions))
        context.check("E: get_survey_properties 照旧", properties.get("sid") is not None, properties.get("sid"))

        # 参与者查询与删除（邀请码撤销走的就是这两个方法，ADR 0016）。
        token = (context.invitations[survey_id])[0]["token"]
        found = client.get_participant_properties(survey_id, {"token": token}, ["tid", "token"])
        context.check("E: get_participant_properties 照旧", found.get("token") == token, found)

        # 收口（/v1/close）：set_survey_properties 设过期。
        closed = close_survey(client, survey_id, datetime.now(timezone.utc))
        context.check("E: 收口照旧（设过期，不停用）", closed.already_closed is False and bool(closed.expires), closed)

        # 答卷导出：写一份答卷再按区间导出。
        column = answer_column(context, survey_id)
        insert_response(context, survey_id, column, ORIGINAL)
        exported = client.call("export_responses",
                               [client.session_key, survey_id, "json", None, "all", "code", "short"])
        context.check("E: export_responses 照旧", isinstance(exported, str) and bool(exported),
                      type(exported).__name__)
    finally:
        client.logout()

    status = docker_probe(context.container)(survey_id)
    context.check("E: 插件通道（newDirectRequest policyStatus）照旧", status is not None, status)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    parser.add_argument("--only-bypass", action="store_true",
                        help="只跑 A/B/C（修复前用它看红，D/E 依赖闸门存在）")
    args = parser.parse_args()

    context = Context(args.container, Database(args.db, args.db_container))
    context.driver = args.db
    now = datetime.now(timezone.utc)

    survey_id = closed_window_survey(context, "RPC bypass {}".format(uuid.uuid4().hex[:6]), now)
    scenario_normal_path_is_denied(context, survey_id)
    scenario_add_response_bypass(context, survey_id)
    scenario_update_response_bypass(context, now)
    if not args.only_bypass:
        scenario_operator_override(context, now)
        scenario_gateway_still_works(context, now)

    print(json.dumps({"failures": context.failures}))
    return 1 if context.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
