#!/usr/bin/env python3

"""WP-04.1 / 04.2 访问规则端到端：定义带 policy 块，经网关发布到真引擎，插件在真实 HTTP 作答里执行。

在宿主机运行，见 platform/deploy/test/run-access-policy.sh。场景（ADR 0016）：

P  插件没激活时发布带策略的问卷 → 网关回读失败并回滚，引擎里不留问卷。
W1 开放时间在未来 → 拒绝（中文提示带本地时刻与时区），原生 startdate 是换算后的 UTC。
W2 已截止（纽约时区）→ 拒绝；清掉原生 expires、伪造 Date 头与 startdate 字段，插件照样拒绝，不落库。
W3 开始时在窗口内、交卷时已截止 → 这次提交被拒，答卷未提交（改客户端时间不延期）。
PW 访问密码：错误被拒、正确进入并交卷；解锁只对本 sid 有效；库里只有哈希；状态端点不泄漏哈希。
L1 按设备限 1 次：同一浏览器第二次被拒；新浏览器可以（已记录的绕过方式）。
L2 按 token 限 1 次：同一 token 换浏览器第二次被拒（插件提示，不是引擎提示）。
D  限时 60 秒：服务端到点拒绝提交、不落库；同一 token 换浏览器也不能重新计时。
C  验证码：原生 usecaptcha=X，进入问卷先要验证码。
N  IP 规则：拒绝 127.0.0.0/8，伪造 X-Forwarded-For 无效。
"""

import argparse
import copy
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.policy.compile import compile_policy  # noqa: E402
from pubgw.policy.password import hash_password  # noqa: E402
from pubgw.policy.probe import HttpPolicyProbe  # noqa: E402
from pubgw.policy.timewindow import load_zone  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402

RESPONDER = "platform/tests/e2e/access_respond.php"
PASSWORD = "Open-Sesame-7"
DURATION_SECONDS = 60
CLOSING_DELAY_SECONDS = 75
SLACK_SECONDS = 6
PLUGIN = "MjyRuntimePolicy"


class Context:
    def __init__(self, container: str, db: Database):
        self.container = container
        self.db = db
        self.failures: List[str] = []
        self.jars = 0

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label, "" if condition else ": {}".format(detail)),
              file=sys.stderr)
        if not condition:
            self.failures.append(label)

    def new_jar(self) -> str:
        self.jars += 1
        return "/tmp/access-e2e-{}-{}.cookies".format(uuid.uuid4().hex[:8], self.jars)

    def respond(self, jar: str, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        completed = subprocess.run(
            ["docker", "exec", "-i", self.container, "php", RESPONDER, jar],
            input=json.dumps({"steps": steps}).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise RuntimeError("responder failed: {}{}".format(completed.stderr.decode(), completed.stdout.decode()))
        return json.loads(completed.stdout.decode("utf-8"))["pages"]

    def submitted(self, survey_id: int) -> int:
        return int(self.db.value("SELECT COUNT(*) FROM lime_responses_{} WHERE submitdate IS NOT NULL".format(survey_id)))


# ------------------------------------------------------------------ 定义与发布


def definition(title: str, policy: Optional[Dict[str, Any]], participants=None) -> Dict[str, Any]:
    payload = {
        "definitionVersion": 1,
        "uuid": str(uuid.uuid4()),
        "title": title,
        "language": "en",
        "theme": "fruity_twentythree",
        "settings": {
            "anonymized": "N", "datestamp": "Y", "savetimings": "N", "ipaddr": "N", "refurl": "N",
            "allowsave": "N", "allowprev": "Y", "alloweditaftercompletion": "N", "format": "A",
            "questionindex": "0",
        },
        "groups": [{
            "uuid": str(uuid.uuid4()),
            "title": "Only",
            "questions": [{"uuid": str(uuid.uuid4()), "code": "QNOTE", "type": "S", "text": "Anything to add?"}],
        }],
    }
    if policy is not None:
        payload["policy"] = copy.deepcopy(policy)
    if participants:
        payload["participants"] = participants
    return payload


def publish(container: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = RemoteControlClient(DockerCurlTransport(container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        return Publisher(client, engine_instance=container, policy_probe=docker_probe(container)).publish(
            SurveyDefinition.from_dict(payload)).to_dict()
    finally:
        client.logout()


def publish_ok(context: Context, payload: Dict[str, Any]) -> int:
    result = publish(context.container, payload)
    if not result["ok"]:
        raise RuntimeError("publish of {} failed at {}: {}".format(payload["title"], result["failedStage"],
                                                                   result["failures"]))
    print("published {} as sid {} (policy {})".format(payload["title"], result["surveyId"],
                                                     result.get("policyDigest")), file=sys.stderr)
    # 平台在发布收尾时自己重算一遍这个摘要再比对（ADR 0016）：回执里的摘要必须正是编译出来的那一个，
    # 否则平台会判发布失败。跨语言的一致性另由 digest-vectors.json 钉住（网关与平台各读一次）。
    compiled = compile_policy(SurveyDefinition.from_dict(payload))
    expected = None if compiled is None else compiled.digest
    context.check("回执的 policyDigest 就是编译出来的摘要",
                  result.get("policyDigest") == expected,
                  {"reported": result.get("policyDigest"), "expected": expected})
    return result["surveyId"]


def docker_fetch(container: str):
    def fetch(url: str) -> bytes:
        completed = subprocess.run(["docker", "exec", container, "curl", "-s", "--max-time", "30", url],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            raise RuntimeError("curl failed: " + completed.stderr.decode("utf-8", "replace"))
        return completed.stdout
    return fetch


def docker_probe(container: str) -> HttpPolicyProbe:
    return HttpPolicyProbe.from_engine_url("http://localhost", fetch=docker_fetch(container))


def rpc(container: str, method: str, *params: Any) -> Any:
    client = RemoteControlClient(DockerCurlTransport(container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        return client.call(method, [client.session_key, *params])
    finally:
        client.logout()


def local(zone_name: str, moment: datetime) -> str:
    return moment.astimezone(load_zone(zone_name)).replace(tzinfo=None).isoformat(timespec="seconds")


def tokens(context: Context, survey_id: int) -> Dict[str, str]:
    """引擎生成的真实 token（网关的 add_participants 让引擎生成 token，见 ADR 0016 缺口）。"""
    return {first: token for first, token in context.db.rows(
        "SELECT firstname, token FROM lime_tokens_{}".format(survey_id))}


def start_url(survey_id: int, token: Optional[str] = None) -> str:
    url = "/index.php/{}?newtest=Y&lang=en".format(survey_id)
    return url + ("&token=" + token if token else "")


def texts(pages: List[Dict[str, Any]]) -> str:
    return " | ".join(page["text"][:200] for page in pages)


# ------------------------------------------------------------------ 场景


def set_plugin_active(context: Context, is_active: bool) -> None:
    context.db.rows("UPDATE lime_plugins SET active = {} WHERE name = '{}'".format(int(is_active), PLUGIN))
    # 引擎把插件清单缓存在 tmp/runtime/cache 里，改库之后要丢掉缓存。
    subprocess.run(["docker", "exec", context.container, "rm", "-rf", "tmp/runtime/cache"], check=True)


def scenario_plugin_missing(context: Context) -> None:
    print("P: plugin inactive -> publish rolls back", file=sys.stderr)
    set_plugin_active(context, False)
    before = int(context.db.value("SELECT COUNT(*) FROM lime_surveys"))
    try:
        payload = definition("P no plugin", {"policyVersion": 1, "limits": {"maxDurationSeconds": 600}})
        result = publish(context.container, payload)
    finally:
        set_plugin_active(context, True)
    context.check("P: publish fails at apply", result["ok"] is False and result["failedStage"] == "apply", result)
    context.check("P: failure names E_POLICY_NOT_ENFORCED",
                  any("E_POLICY_NOT_ENFORCED" in failure for failure in result["failures"]), result["failures"])
    context.check("P: rolled back, no survey left", result["rolledBack"] is True
                  and int(context.db.value("SELECT COUNT(*) FROM lime_surveys")) == before, result)


def scenario_not_open(context: Context, now: datetime) -> None:
    print("W1: window opens in the future", file=sys.stderr)
    opens = now + timedelta(days=30)
    policy = {"policyVersion": 1, "window": {"opensAt": local("Asia/Shanghai", opens), "timezone": "Asia/Shanghai"}}
    sid = publish_ok(context, definition("W1 future", policy))
    pages = context.respond(context.new_jar(), [{"get": start_url(sid)}])
    context.check("W1: denied before opening", pages[0]["kind"] == "message" and "尚未开放" in pages[0]["text"], texts(pages))
    context.check("W1: message shows local time and zone",
                  local("Asia/Shanghai", opens).replace("T", " ") in pages[0]["text"] and "Asia/Shanghai" in pages[0]["text"],
                  texts(pages))
    stored = context.db.value("SELECT startdate FROM lime_surveys WHERE sid = {}".format(sid))
    context.check("W1: native startdate is the UTC instant",
                  stored.startswith(opens.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")), stored)


def scenario_closed(context: Context, now: datetime) -> None:
    print("W2: closed window (America/New_York), native expires removed, forged client time", file=sys.stderr)
    closes = now - timedelta(hours=1)
    policy = {"policyVersion": 1, "window": {"closesAt": local("America/New_York", closes), "timezone": "America/New_York"}}
    sid = publish_ok(context, definition("W2 closed", policy))
    stored = context.db.value("SELECT expires FROM lime_surveys WHERE sid = {}".format(sid))
    context.check("W2: native expires is the UTC instant",
                  stored.startswith(closes.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")), stored)
    # 插件必须自己守住：把原生的 expires 清掉，客户端再报一个"还没截止"的时间。
    rpc(context.container, "set_survey_properties", sid, {"expires": ""})
    context.check("W2: native expires cleared",
                  context.db.value("SELECT COUNT(*) FROM lime_surveys WHERE sid = {} AND expires IS NULL".format(sid)) == "1")
    forged = (now - timedelta(days=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    pages = context.respond(context.new_jar(), [
        {"get": start_url(sid), "headers": ["Date: " + forged, "X-Client-Time: " + forged]},
    ])
    context.check("W2: denied after closing despite forged Date header",
                  pages[0]["kind"] == "message" and "截止" in pages[0]["text"], texts(pages))
    context.check("W2: message shows New York local time", "America/New_York" in pages[0]["text"], texts(pages))
    context.check("W2: nothing submitted", context.submitted(sid) == 0)


def start_closing_window(context: Context, now: datetime) -> Dict[str, Any]:
    print("W3: window closes {}s after publishing; respondent starts inside it".format(CLOSING_DELAY_SECONDS),
          file=sys.stderr)
    closes = datetime.now(timezone.utc) + timedelta(seconds=CLOSING_DELAY_SECONDS)
    policy = {"policyVersion": 1, "window": {"closesAt": local("Asia/Shanghai", closes), "timezone": "Asia/Shanghai"}}
    sid = publish_ok(context, definition("W3 closing", policy))
    jar = context.new_jar()
    form = "/tmp/access-e2e-w3-{}.json".format(sid)
    pages = context.respond(jar, [{"get": start_url(sid)}, {"saveForm": form}])
    context.check("W3: allowed inside the window", pages[0]["kind"] == "survey", texts(pages))
    return {"sid": sid, "jar": jar, "form": form, "after": closes + timedelta(seconds=SLACK_SECONDS)}


def finish_closing_window(context: Context, state: Dict[str, Any]) -> None:
    pages = context.respond(state["jar"], [{"postSaved": state["form"], "move": "movesubmit"}])
    context.check("W3: submission after closing is rejected", pages[0]["kind"] == "message"
                  and "截止" in pages[0]["text"], texts(pages))
    context.check("W3: answer not submitted", context.submitted(state["sid"]) == 0)


def scenario_password(context: Context) -> None:
    print("PW: access password", file=sys.stderr)
    policy = {"policyVersion": 1, "access": {"passwordHash": hash_password(PASSWORD)}}
    sid = publish_ok(context, definition("PW guarded", policy))
    sibling = publish_ok(context, definition("PW sibling", policy))
    jar = context.new_jar()
    pages = context.respond(jar, [
        {"get": start_url(sid)},
        {"password": "wrong-password"},
        {"password": PASSWORD},
        {"submit": {}, "move": "movesubmit"},
        {"get": start_url(sibling)},
    ])
    context.check("PW: password page first", pages[0]["kind"] == "password", texts(pages))
    context.check("PW: wrong password rejected", pages[1]["kind"] == "password" and "不正确" in pages[1]["text"],
                  texts(pages))
    context.check("PW: right password opens the survey", pages[2]["kind"] == "survey", texts(pages))
    context.check("PW: survey completes", pages[3]["kind"] == "completed", texts(pages))
    context.check("PW: unlock is per survey (sibling still asks)", pages[4]["kind"] == "password", texts(pages))
    context.check("PW: one submitted response", context.submitted(sid) == 1)
    stored = context.db.value(
        "SELECT value FROM lime_plugin_settings WHERE model = 'Survey' AND model_id = {}".format(sid))
    context.check("PW: engine stores only the hash", "pbkdf2-sha256$" in stored and PASSWORD not in stored)
    status = docker_fetch(context.container)(
        "http://localhost/index.php/plugins/direct?plugin={}&function=policyStatus&sid={}".format(PLUGIN, sid))
    context.check("PW: status endpoint does not leak the hash", b"pbkdf2" not in status and b"policyDigest" in status,
                  status[:200])


def scenario_device_limit(context: Context) -> None:
    print("L1: one response per device", file=sys.stderr)
    sid = publish_ok(context, definition("L1 device", {"policyVersion": 1, "limits": {"responses": [{"by": "device", "max": 1}]}}))
    jar = context.new_jar()
    first = context.respond(jar, [{"get": start_url(sid)}, {"submit": {}, "move": "movesubmit"}])
    again = context.respond(jar, [{"get": start_url(sid)}])
    other = context.respond(context.new_jar(), [{"get": start_url(sid)}])
    context.check("L1: first response completes", first[1]["kind"] == "completed", texts(first))
    context.check("L1: same browser denied", again[0]["kind"] == "message" and "次数上限" in again[0]["text"], texts(again))
    context.check("L1: a fresh browser gets in (documented bypass)", other[0]["kind"] == "survey", texts(other))
    context.check("L1: exactly one submitted", context.submitted(sid) == 1)


def scenario_token_limit(context: Context) -> None:
    print("L2: one response per token", file=sys.stderr)
    participants = [{"firstname": "A"}, {"firstname": "B"}]
    sid = publish_ok(context, definition("L2 token", {
        "policyVersion": 1, "access": {"invitationRequired": True},
        "limits": {"responses": [{"by": "token", "max": 1}]}}, participants))
    codes = tokens(context, sid)
    context.check("L2: closed access (access_mode=C)",
                  context.db.value("SELECT access_mode FROM lime_surveys WHERE sid = {}".format(sid)) == "C")
    anonymous = context.respond(context.new_jar(), [{"get": start_url(sid)}])
    context.check("L2: no invitation code, no survey", anonymous[0]["kind"] != "survey", texts(anonymous))
    first = context.respond(context.new_jar(), [{"get": start_url(sid, codes["A"])}, {"submit": {}, "move": "movesubmit"}])
    again = context.respond(context.new_jar(), [{"get": start_url(sid, codes["A"])}])
    other = context.respond(context.new_jar(), [{"get": start_url(sid, codes["B"])}])
    context.check("L2: first response completes", first[1]["kind"] == "completed", texts(first))
    context.check("L2: same token in a new browser denied by the plugin",
                  again[0]["kind"] == "message" and "次数上限" in again[0]["text"], texts(again))
    context.check("L2: other token gets in", other[0]["kind"] == "survey", texts(other))


def start_duration(context: Context) -> Dict[str, Any]:
    print("D: {}s time limit, token identity".format(DURATION_SECONDS), file=sys.stderr)
    sid = publish_ok(context, definition("D timed", {
        "policyVersion": 1, "access": {"invitationRequired": True},
        "limits": {"maxDurationSeconds": DURATION_SECONDS}}, [{"firstname": "D"}]))
    token = tokens(context, sid)["D"]
    jar = context.new_jar()
    form = "/tmp/access-e2e-d-{}.json".format(sid)
    pages = context.respond(jar, [{"get": start_url(sid, token)}, {"saveForm": form}])
    context.check("D: allowed at the start", pages[0]["kind"] == "survey", texts(pages))
    return {"sid": sid, "jar": jar, "form": form, "token": token,
            "after": datetime.now(timezone.utc) + timedelta(seconds=DURATION_SECONDS + SLACK_SECONDS)}


def finish_duration(context: Context, state: Dict[str, Any]) -> None:
    pages = context.respond(state["jar"], [{"postSaved": state["form"], "move": "movesubmit"}])
    fresh = context.respond(context.new_jar(), [{"get": start_url(state["sid"], state["token"])}])
    context.check("D: late submission rejected on the server", pages[0]["kind"] == "message"
                  and "限时 1 分钟" in pages[0]["text"], texts(pages))
    context.check("D: answer not submitted", context.submitted(state["sid"]) == 0)
    context.check("D: same token in a new browser cannot restart the clock",
                  fresh[0]["kind"] == "message" and "限时" in fresh[0]["text"], texts(fresh))


def scenario_captcha(context: Context) -> None:
    print("C: captcha", file=sys.stderr)
    sid = publish_ok(context, definition("C captcha", {"policyVersion": 1, "access": {"captcha": True}}))
    pages = context.respond(context.new_jar(), [{"get": start_url(sid)}])
    context.check("C: native usecaptcha is X",
                  context.db.value("SELECT usecaptcha FROM lime_surveys WHERE sid = {}".format(sid)) == "X")
    context.check("C: captcha asked before the survey", pages[0]["hasCaptcha"] is True, texts(pages))


def scenario_network(context: Context) -> None:
    print("N: IP deny list, forged X-Forwarded-For", file=sys.stderr)
    policy = {"policyVersion": 1, "network": {"denyIps": ["127.0.0.0/8", "::1"]}}
    sid = publish_ok(context, definition("N network", policy))
    pages = context.respond(context.new_jar(), [
        {"get": start_url(sid), "headers": ["X-Forwarded-For: 8.8.8.8", "Client-IP: 8.8.4.4"]},
    ])
    context.check("N: loopback denied even with forged forwarding headers",
                  pages[0]["kind"] == "message" and "当前网络" in pages[0]["text"], texts(pages))


def wait_until(moment: datetime) -> None:
    delay = (moment - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        print("waiting {:.0f}s for server-side time to pass".format(delay), file=sys.stderr)
        time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    args = parser.parse_args()
    context = Context(args.container, Database(args.db, args.db_container))
    now = datetime.now(timezone.utc)

    scenario_plugin_missing(context)
    closing = start_closing_window(context, now)
    timed = start_duration(context)
    scenario_not_open(context, now)
    scenario_closed(context, now)
    scenario_password(context)
    scenario_device_limit(context)
    scenario_token_limit(context)
    scenario_captcha(context)
    scenario_network(context)
    wait_until(max(closing["after"], timed["after"]))
    finish_closing_window(context, closing)
    finish_duration(context, timed)

    print(json.dumps({"failures": context.failures}))
    return 1 if context.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
