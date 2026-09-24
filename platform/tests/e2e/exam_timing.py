#!/usr/bin/env python3

"""WP-09.2 服务端计时与强制交卷端到端：时间由服务端说了算，到点由服务端收卷。

在宿主机运行，见 platform/deploy/test/run-exam-timing.sh。

ADR 0007 留下两项，这里补上并验证：

- 「截止时刻只有到点即拒，没有到点自动交卷」——到点即拒对考试是错的：作答者
  已经落进答卷表的答案会永远停在 submitdate IS NULL，等于白考一场。
- 「断线重连的作答者会看到拒绝页而不是剩余时间」——剩余时间现在由服务端下发。

## 场景

T1 进场后服务端报剩余时间；截止时刻按服务端时钟定死。
T2 **改客户端时钟**：Date 与 X-Client-Time 指向遥远的过去，服务端报的剩余时间不变。
T3 答完第一页，答卷表里出现一份未交卷的答卷。
T4 **断线续考**：换个浏览器、同一准考证回来，剩余时间接着原来的算，不是重新一整场。
T5 （等到点）
T6 **重放旧会话**：到点前存下的表单到点后再提交——被拒，且那份卷已被服务端强制交掉，
   submitdate 正是**截止时刻**而不是回收作业跑起来的时刻。
T7 **直接 POST 绕过计时**：不进场，拿保存的表单在全新浏览器里直接提交——被拒，不落库。
T8 **伪造交卷时间**：POST 里塞 submitdate / startdate / datestamp——不被采信。
T9 **关掉浏览器的人由 cron 收卷**：没有后续请求，beforeSurveyPage 挂不上，
   cron 跑完后他那份卷的 submitdate 同样是截止时刻。
"""

import argparse
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
from pubgw.policy.probe import HttpPolicyProbe  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402

RESPONDER = "platform/tests/e2e/access_respond.php"
PLUGIN = "MjyRuntimePolicy"
#: 契约 survey-access-policy-v1 的下限就是 60 秒，考试用例取下限以免整轮太慢。
DURATION_SECONDS = 60
#: 到点之后再多等几秒，避开秒级边界。
SLACK_SECONDS = 8
FORGED_TIME = "Mon, 01 Jan 2001 00:00:00 GMT"


class Context:
    def __init__(self, container: str, db: Database):
        self.container = container
        self.db = db
        self.failures: List[str] = []
        self.jars = 0

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label,
                                   "" if condition else ": {}".format(detail)), file=sys.stderr)
        if not condition:
            self.failures.append(label)

    def new_jar(self) -> str:
        self.jars += 1
        return "/tmp/exam-timing-{}-{}.cookies".format(uuid.uuid4().hex[:8], self.jars)

    def respond(self, jar: str, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        completed = subprocess.run(
            ["docker", "exec", "-i", self.container, "php", RESPONDER, jar],
            input=json.dumps({"steps": steps}).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise RuntimeError("responder failed: {}{}".format(
                completed.stderr.decode(), completed.stdout.decode()))
        return json.loads(completed.stdout.decode("utf-8"))["pages"]

    def exam_time(self, jar: str, survey_id: int, headers: Optional[List[str]] = None) -> Dict[str, Any]:
        """作答页问服务端"还剩多少秒"。带上同一个 Cookie 罐才是同一个人。"""
        url = ("http://localhost/index.php/plugins/direct"
               "?plugin={}&function=examTime&sid={}".format(PLUGIN, survey_id))
        command = ["docker", "exec", self.container, "curl", "-s", "--max-time", "30",
                   "-b", jar, "-c", jar]
        for header in headers or []:
            command += ["-H", header]
        body = subprocess.run(command + [url], stdout=subprocess.PIPE, check=True).stdout.decode()
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError("examTime 没有返回 JSON：" + body[:300])

    def attempt(self, survey_id: int, token: str) -> Dict[str, str]:
        """考场记录里这位考生那一行：答卷行号与截止时刻都以它为准，
        不靠答卷表的下标去猜（进场就会建行，下标对不上人）。"""
        rows = self.db.rows(
            "SELECT response_id, deadline_at, state FROM lime_mjyruntimepolicy_exam_attempt "
            "WHERE survey_id = {} AND session_key = 'token:{}'".format(survey_id, token))
        if not rows:
            return {}
        return {"response_id": rows[0][0], "deadline_at": rows[0][1], "state": rows[0][2]}

    def submitdate_of(self, survey_id: int, response_id: str) -> Optional[str]:
        value = self.db.value(
            "SELECT submitdate FROM lime_responses_{} WHERE id = {}".format(survey_id, response_id))
        return None if value in ("", "NULL", None) else value[:19]

    def responses(self, survey_id: int) -> List[List[str]]:
        return self.db.rows(
            "SELECT id, submitdate FROM lime_responses_{} ORDER BY id".format(survey_id))

    def submitted_count(self, survey_id: int) -> int:
        return int(self.db.value(
            "SELECT COUNT(*) FROM lime_responses_{} WHERE submitdate IS NOT NULL".format(survey_id)) or 0)


# ------------------------------------------------------------------ 定义与发布


def definition(title: str, participants: List[Dict[str, str]]) -> Dict[str, Any]:
    """两个题组：第一页交上去就会在答卷表里留下一份未交卷的答卷，
    强制交卷才有东西可交。"""
    return {
        "definitionVersion": 1,
        "uuid": str(uuid.uuid4()),
        "title": title,
        "language": "en",
        "theme": "fruity_twentythree",
        "settings": {
            "anonymized": "N", "datestamp": "Y", "savetimings": "N", "ipaddr": "N", "refurl": "N",
            "allowsave": "N", "allowprev": "Y", "alloweditaftercompletion": "N", "format": "G",
            "questionindex": "0",
            # 断线续考的前提：同一准考证回来接着填同一份答卷，而不是另起一份。
            "tokenanswerspersistence": "Y",
        },
        "groups": [
            {
                "uuid": str(uuid.uuid4()), "title": "第一页",
                "questions": [{"uuid": str(uuid.uuid4()), "code": "QONE", "type": "S", "text": "第一题"}],
            },
            {
                "uuid": str(uuid.uuid4()), "title": "第二页",
                "questions": [{"uuid": str(uuid.uuid4()), "code": "QTWO", "type": "S", "text": "第二题"}],
            },
        ],
        "policy": {
            "policyVersion": 1,
            "access": {"invitationRequired": True},
            "limits": {"responses": [{"by": "token", "max": 1}],
                       "maxDurationSeconds": DURATION_SECONDS},
        },
        "participants": participants,
    }


def docker_fetch(container: str):
    def fetch(url: str) -> bytes:
        completed = subprocess.run(["docker", "exec", container, "curl", "-s", "--max-time", "30", url],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            raise RuntimeError("curl failed: " + completed.stderr.decode("utf-8", "replace"))
        return completed.stdout
    return fetch


def publish_ok(context: Context, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = RemoteControlClient(DockerCurlTransport(context.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        probe = HttpPolicyProbe.from_engine_url("http://localhost", fetch=docker_fetch(context.container))
        result = Publisher(client, engine_instance=context.container,
                           policy_probe=probe).publish(SurveyDefinition.from_dict(payload)).to_dict()
    finally:
        client.logout()
    if not result["ok"]:
        raise RuntimeError("publish failed at {}: {}".format(result["failedStage"], result["failures"]))
    print("published {} as sid {}".format(payload["title"], result["surveyId"]), file=sys.stderr)
    return result


def start_url(survey_id: int, token: str) -> str:
    return "/index.php/{}?newtest=Y&lang=en&token={}".format(survey_id, token)


def resume_url(survey_id: int, token: str) -> str:
    """续考用：不带 newtest=Y。带上它是"重新开考"，引擎会另起一份答卷。"""
    return "/index.php/{}?lang=en&token={}".format(survey_id, token)


def texts(pages: List[Dict[str, Any]]) -> str:
    return " | ".join(page["text"][:160] for page in pages)


def run_cron(context: Context) -> None:
    subprocess.run(["docker", "exec", context.container, "php",
                    "application/commands/console.php", "plugin", "cron"],
                   stdout=subprocess.PIPE, check=True)


# ------------------------------------------------------------------ 场景


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    args = parser.parse_args()
    context = Context(args.container, Database(args.db, args.db_container))

    receipt = publish_ok(context, definition(
        "WP-09.2 服务端计时",
        [{"ref": "A", "firstname": "A"}, {"ref": "B", "firstname": "B"}, {"ref": "C", "firstname": "C"}]))
    sid = receipt["surveyId"]
    tokens = {item["ref"]: item["token"] for item in receipt.get("invitations") or []}
    context.check("发布回执给出了三张准考证", sorted(tokens) == ["A", "B", "C"], tokens)

    # ------------------------------------------------- T1/T3 进场、答第一页、剩余时间
    print("T1: 进场并答完第一页", file=sys.stderr)
    jar_a = context.new_jar()
    saved_form = "/tmp/exam-timing-a-{}.json".format(sid)
    pages = context.respond(jar_a, [
        {"get": start_url(sid, tokens["A"])},
        {"submit": {}, "move": "movenext"},
        {"saveForm": saved_form},
    ])
    context.check("T1: 进得去", pages[0]["kind"] == "survey", texts(pages))
    context.check("T1: 到了第二页", pages[1]["kind"] == "survey", texts(pages))

    first = context.exam_time(jar_a, sid)
    context.check("T1: 服务端报了剩余秒数",
                  0 < first.get("remainingSeconds", 0) <= DURATION_SECONDS, first)
    context.check("T1: 还没到点", first.get("expired") is False, first)
    # 刻意不下发"开始时刻＋总时长"：那两样凑一起就是一台可以被改回去的客户端计时器。
    context.check("T1: 下发的只有剩余秒数与服务端时刻，没有开始时刻与总时长",
                  not ({"startedAt", "durationSeconds", "deadlineAt"} & set(first)), sorted(first))

    attempt_a = context.attempt(sid, tokens["A"])
    context.check("T3: 考场记录指向一份答卷", attempt_a.get("response_id") not in (None, "", "NULL"),
                  attempt_a)
    context.check("T3: 那份答卷还没交", context.submitdate_of(sid, attempt_a["response_id"]) is None,
                  attempt_a)
    context.check("T3: 此时没有任何人交卷", context.submitted_count(sid) == 0, context.responses(sid))

    # ---------------------------------------------------------- T2 改客户端时钟
    print("T2: 改客户端时钟", file=sys.stderr)
    forged = context.exam_time(jar_a, sid,
                               headers=["Date: " + FORGED_TIME, "X-Client-Time: " + FORGED_TIME])
    context.check("T2: 伪造的客户端时刻不影响服务端算出的剩余时间",
                  abs(forged["remainingSeconds"] - first["remainingSeconds"]) <= 5,
                  {"before": first, "after": forged})

    # ---------------------------------------------------------- T4 断线续考
    print("T4: 断线续考——换浏览器、同一准考证", file=sys.stderr)
    jar_a2 = context.new_jar()
    resumed_pages = context.respond(jar_a2, [{"get": resume_url(sid, tokens["A"])}])
    context.check("T4: 换了浏览器还能进", resumed_pages[0]["kind"] == "survey", texts(resumed_pages))
    resumed = context.exam_time(jar_a2, sid)
    context.check("T4: 换了浏览器仍在考试中", resumed.get("expired") is False, resumed)
    context.check("T4: 剩余时间接着原来的算，不是重新一整场",
                  resumed["remainingSeconds"] <= first["remainingSeconds"],
                  {"first": first, "resumed": resumed})
    context.check("T4: 续考没有把截止时刻往后挪",
                  context.attempt(sid, tokens["A"])["deadline_at"] == attempt_a["deadline_at"],
                  {"before": attempt_a, "after": context.attempt(sid, tokens["A"])})

    # C 号考生只答一页就关掉浏览器，留给 T9 的 cron 收。
    print("T9 准备：C 号答一页就关掉浏览器", file=sys.stderr)
    context.respond(context.new_jar(), [
        {"get": start_url(sid, tokens["C"])},
        {"submit": {}, "move": "movenext"},
    ])
    attempt_c = context.attempt(sid, tokens["C"])
    context.check("T9 准备：C 号也有一份未交的答卷",
                  attempt_c.get("response_id") not in (None, "", "NULL")
                  and context.submitdate_of(sid, attempt_c["response_id"]) is None, attempt_c)

    # ---------------------------------------------------------- 等到点
    deadline_passed = datetime.now(timezone.utc) + timedelta(seconds=DURATION_SECONDS + SLACK_SECONDS)
    delay = (deadline_passed - datetime.now(timezone.utc)).total_seconds()
    print("等 {:.0f} 秒让服务端的考试时间走完".format(max(0, delay)), file=sys.stderr)
    if delay > 0:
        time.sleep(delay)

    # ---------------------------------------------------------- T6 重放旧会话
    print("T6: 重放到点前存下的表单", file=sys.stderr)
    pages = context.respond(jar_a, [{"postSaved": saved_form, "move": "movesubmit"}])
    context.check("T6: 到点后的提交被拒",
                  pages[0]["kind"] == "message" and "时间已到" in pages[0]["text"], texts(pages))

    attempt_a = context.attempt(sid, tokens["A"])
    submitted_at = context.submitdate_of(sid, attempt_a["response_id"])
    context.check("T6: 那份卷已经被服务端强制交掉了", submitted_at is not None, attempt_a)
    # 写进去的必须是截止时刻，而不是"收卷动作碰巧跑起来的时刻"——
    # 作答者的时间就是在截止时刻用完的，这样结果也与收卷时机无关。
    context.check("T6: submitdate 正是截止时刻，不是收卷时刻",
                  submitted_at == attempt_a["deadline_at"][:19],
                  {"submitdate": submitted_at, "deadline": attempt_a["deadline_at"]})
    context.check("T6: 考场记录记成了强制交卷", attempt_a["state"] == "forced", attempt_a)

    late = context.exam_time(jar_a, sid)
    context.check("T6: 服务端报已到点且剩余为 0",
                  late.get("expired") is True and late.get("remainingSeconds") == 0, late)

    # ---------------------------------------------------------- T7 直接 POST 绕过计时
    print("T7: 不进场，直接 POST", file=sys.stderr)
    before = context.responses(sid)
    pages = context.respond(context.new_jar(), [{"postSaved": saved_form, "move": "movesubmit"}])
    context.check("T7: 直接 POST 被拒", pages[0]["kind"] == "message", texts(pages))
    context.check("T7: 没有新答卷落库", len(context.responses(sid)) == len(before), context.responses(sid))

    # ---------------------------------------------------------- T8 伪造交卷时间
    print("T8: POST 里塞时间字段", file=sys.stderr)
    context.respond(context.new_jar(), [
        {"get": start_url(sid, tokens["B"])},
        {"submit": {}, "move": "movenext",
         "extra": {"submitdate": "2099-01-01 00:00:00", "startdate": "2099-01-01 00:00:00",
                   "datestamp": "2099-01-01 00:00:00", "interviewtime": "1"}},
    ])
    forged_count = context.db.value(
        "SELECT COUNT(*) FROM lime_responses_{} WHERE submitdate > '2090-01-01'".format(sid))
    context.check("T8: 伪造的交卷时间没有被采信", forged_count in ("0", ""), forged_count)

    # ---------------------------------------------------------- T9 cron 收卷
    print("T9: cron 给关掉浏览器的人收卷", file=sys.stderr)
    run_cron(context)
    attempt_c = context.attempt(sid, tokens["C"])
    submitted_c = context.submitdate_of(sid, attempt_c["response_id"])
    context.check("T9: 关掉浏览器的那份卷也被收了", submitted_c is not None, attempt_c)
    context.check("T9: 收上来的 submitdate 同样是截止时刻",
                  submitted_c == attempt_c["deadline_at"][:19],
                  {"submitdate": submitted_c, "deadline": attempt_c["deadline_at"]})
    run_cron(context)
    context.check("T9: 再跑一次 cron 不会重复改动",
                  context.submitdate_of(sid, attempt_c["response_id"]) == submitted_c)

    print(json.dumps({"failures": context.failures}))
    return 1 if context.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
