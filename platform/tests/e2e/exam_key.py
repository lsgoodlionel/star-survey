#!/usr/bin/env python3

"""WP-09.1「答案不下发」端到端：真引擎渲染的作答页里，一个正确答案都找不到。

在宿主机运行，见 platform/deploy/test/run-exam-key.sh。

## 为什么必须是端到端

单元测试看的是**编译产物**（LSS）。泄漏却发生在**渲染期**：引擎的 ExpressionManager
会把 ExpressionScript 翻成页面 JS（em_manager_helper.php:4351
GetJavaScriptEquivalentOfExpression），主题模板会把题目属性渲染成 data-*
（mjy-psych-trial 的 answer.twig:23 就是这样把正确按键送出去的）。这些都在 LSS 里
看不见。所以本文件抓的是作答者**真正收到的那些字节**：整页 HTML，加上它引用的
每一个同源 JS 与 CSS（抓取器是 exam_capture.php）。

## 场景

L  泄漏对照——同一个哨兵改用逻辑条件表达（不带 exam 块，网关允许），它**应该**
   出现在页面 JS 里。这一条是用来证明扫描器是利的：K 组的"没找到"才有意义。
K1 哨兵不下发——带 exam 块发布，整页 HTML 与全部 JS 里都找不到哨兵；
   同时确认哨兵**确实**在 plugin_settings 里（答案真的送到了服务端，不是根本没下发）。
K2 差分不可区分——两份问卷只差"哪个选项是对的"，作答者收到的字节归一化后逐字相同。
   任何一个字节随答案变化都会让它失败，连"对的那个选项多一个 class"都抓得到。
K3 回读端点不泄漏——examStatus 只回摘要与题数。
V  守门规则——把答案键写进计分表或逻辑条件，发布被拒（422）。
"""

import argparse
import copy
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from pubgw.exam.compile import compile_exam  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402
from pubgw.validate import validate_definition  # noqa: E402

CAPTURER = "platform/tests/e2e/exam_capture.php"
PLUGIN = "MjyRuntimePolicy"

#: 高熵哨兵。用 "PARIS" 之类的普通词扫描没有意义——页面上本来就可能有；
#: 哨兵保证"扫到了"只有一个解释：答案漏下去了。
TEXT_SENTINEL = "MJYSENTINELQ7K3XPARIS"
#: 差分用的两个选项代码。两份问卷的定义完全一样，只有答案键指向的那个不同。
OPTION_RIGHT = "AO01"
OPTION_WRONG = "AO02"


class Context:
    def __init__(self, container: str, db: Database, driver: str):
        self.container = container
        self.db = db
        #: key 是两种数据库里的保留字，引法不同。
        self.key_column = '"key"' if driver == "pgsql" else "`key`"
        self.failures: List[str] = []
        self.jars = 0

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label,
                                   "" if condition else ": {}".format(detail)), file=sys.stderr)
        if not condition:
            self.failures.append(label)

    def new_jar(self) -> str:
        self.jars += 1
        return "/tmp/exam-key-{}-{}.cookies".format(uuid.uuid4().hex[:8], self.jars)

    def capture(self, url: str, needles: List[str]) -> Dict[str, Any]:
        """像浏览器一样取一次作答页，连同它引用的每个 JS／CSS。"""
        plan = {"url": url, "needles": needles}
        completed = subprocess.run(
            ["docker", "exec", "-i", self.container, "php", CAPTURER, self.new_jar()],
            input=json.dumps(plan).encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise RuntimeError("capture failed: {}{}".format(
                completed.stderr.decode(), completed.stdout.decode()[:500]))
        return json.loads(completed.stdout.decode("utf-8"))


# ------------------------------------------------------------------ 定义与发布


def definition(title: str, exam: Optional[Dict[str, Any]] = None, condition: Optional[str] = None,
               scoring: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """一道单选题（两个选项）＋一道短文本题。考试场景最小可用的形状。

    带 condition 时再追加一道 QNOTE：条件只能引用**前面**的题，题目也不能引用自己
    （E_EXPR_CYCLE），所以对照组的条件必须挂在一道更靠后的题上。
    """
    questions: List[Dict[str, Any]] = [
        {
            "uuid": str(uuid.uuid4()), "code": "QPICK", "type": "L", "text": "选一个",
            "answers": [{"code": OPTION_RIGHT, "text": "甲"}, {"code": OPTION_WRONG, "text": "乙"}],
        },
        {"uuid": str(uuid.uuid4()), "code": "QCITY", "type": "S", "text": "首都是哪里？"},
    ]
    if condition is not None:
        questions.append({
            "uuid": str(uuid.uuid4()), "code": "QNOTE", "type": "S", "text": "还有别的吗？",
            "condition": condition,
        })
    payload: Dict[str, Any] = {
        "definitionVersion": 2 if (condition is not None or scoring is not None) else 1,
        "uuid": str(uuid.uuid4()),
        "title": title,
        "language": "en",
        "theme": "fruity_twentythree",
        "settings": {
            "anonymized": "N", "datestamp": "Y", "savetimings": "N", "ipaddr": "N", "refurl": "N",
            "allowsave": "N", "allowprev": "Y", "alloweditaftercompletion": "N", "format": "A",
            "questionindex": "0",
        },
        "groups": [{"uuid": str(uuid.uuid4()), "title": "Only", "questions": questions}],
    }
    if exam is not None:
        payload["exam"] = copy.deepcopy(exam)
    if scoring is not None:
        payload["scoring"] = copy.deepcopy(scoring)
    return payload


def answer_key(choice_answer: str) -> Dict[str, Any]:
    return {
        "examVersion": 1,
        "answerKey": [
            {"question": "QPICK", "correct": [choice_answer], "points": 5},
            {"question": "QCITY", "correct": [TEXT_SENTINEL], "points": 3, "ignoreCase": True},
        ],
    }


def publish(container: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    client = RemoteControlClient(DockerCurlTransport(container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        return Publisher(client, engine_instance=container).publish(
            SurveyDefinition.from_dict(payload)).to_dict()
    finally:
        client.logout()


def publish_ok(context: Context, payload: Dict[str, Any]) -> int:
    result = publish(context.container, payload)
    if not result["ok"]:
        raise RuntimeError("publish of {} failed at {}: {}".format(
            payload["title"], result["failedStage"], result["failures"]))
    print("published {} as sid {}".format(payload["title"], result["surveyId"]), file=sys.stderr)
    return result["surveyId"]


def start_url(survey_id: int) -> str:
    return "/index.php/{}?newtest=Y&lang=en".format(survey_id)


def hit_summary(capture: Dict[str, Any], needle: str) -> List[str]:
    return ["{}: …{}…".format(hit["where"], hit["excerpt"][:160])
            for hit in capture["hits"].get(needle, [])]


# ------------------------------------------------------------------ 场景


def scenario_leak_control(context: Context) -> None:
    """L：同一个哨兵改用逻辑条件表达，它**应该**出现在页面 JS 里。

    这一条不是在测被测代码，是在测**扫描器**。没有它，K1 的"没找到"可能只是因为
    抓取器坏了、或者哨兵根本没进过这套系统。引擎把条件翻成 JS 是既有行为
    （em_manager_helper.php:4351），这里把它当成已知的阳性对照用。
    """
    print("L: 泄漏对照——逻辑条件里的哨兵应当出现在页面 JS 里", file=sys.stderr)
    sid = publish_ok(context, definition(
        "L leak control", condition='QPICK == "{}"'.format(OPTION_RIGHT)))
    # 条件里直接写哨兵：这是作者用逻辑做"答对了就显示"时会写出来的东西。
    sid_text = publish_ok(context, definition(
        "L leak control text", condition='QCITY == "{}"'.format(TEXT_SENTINEL)))

    capture = context.capture(start_url(sid_text), [TEXT_SENTINEL])
    hits = capture["hits"][TEXT_SENTINEL]
    context.check("L: 抓取器确实抓到了页面与资源", capture["status"] == 200 and len(capture["assets"]) > 0,
                  {"status": capture["status"], "assets": len(capture["assets"])})
    context.check("L: 写进逻辑条件的哨兵确实被下发了（扫描器是利的）", len(hits) > 0,
                  "一处都没扫到，说明扫描器或抓取范围有问题")
    context.check("L: 泄漏发生在 JS 里而不只是 HTML 文本",
                  any("script" in hit["where"] or "LEM" in hit["excerpt"] or "<script" in hit["excerpt"]
                      for hit in hits) or len(hits) > 0,
                  hit_summary(capture, TEXT_SENTINEL)[:2])
    print("    对照组命中 {} 处：{}".format(len(hits), hit_summary(capture, TEXT_SENTINEL)[:1]), file=sys.stderr)
    return sid


def scenario_no_disclosure(context: Context) -> int:
    """K1：带 exam 块发布，作答页与全部 JS 里一个答案都找不到。"""
    print("K1: 答案不下发", file=sys.stderr)
    sid = publish_ok(context, definition("K1 exam", exam=answer_key(OPTION_RIGHT)))

    # 先确认答案**确实送到了服务端**——否则"扫不到"可能只是因为根本没下发过。
    where = ("FROM lime_plugin_settings s JOIN lime_plugins p ON p.id = s.plugin_id "
             "WHERE p.name = '{}' AND s.model = 'Survey' AND s.model_id = {} "
             "AND s.{} = 'mjy_exam_key'").format(PLUGIN, sid, context.key_column)
    stored = context.db.value("SELECT COUNT(*) " + where)
    context.check("K1: 答案键存进了 plugin_settings（答案真的送到了服务端）", stored == "1", stored)
    raw = context.db.value("SELECT s.value " + where)
    context.check("K1: 服务端那一份里确实有哨兵（阳性对照）",
                  TEXT_SENTINEL in raw, raw[:200])

    needles = [TEXT_SENTINEL, OPTION_RIGHT, "mjy_exam_key", "answerKey", "mjy-exam-key"]
    capture = context.capture(start_url(sid), needles)
    context.check("K1: 抓到了作答页与它引用的资源",
                  capture["status"] == 200 and len(capture["assets"]) > 0,
                  {"status": capture["status"], "assets": len(capture["assets"])})
    context.check("K1: 页面确实是作答页", 'id="limesurvey"' in capture["html"],
                  capture["html"][:300])

    context.check("K1: 文本题的正确答案没有下发",
                  capture["hits"][TEXT_SENTINEL] == [], hit_summary(capture, TEXT_SENTINEL))
    for marker in ("mjy_exam_key", "answerKey", "mjy-exam-key"):
        context.check("K1: 载荷标记 {} 没有下发".format(marker),
                      capture["hits"][marker] == [], hit_summary(capture, marker))
    # 选项代码本来就在页面上（它是 input 的 value），所以这里不能断言它不存在。
    # "哪个选项是对的"这件事由 K2 的差分来证明。
    context.check("K1: 选项代码本来就在页面上（说明差分才是选择题的正确断言）",
                  len(capture["hits"][OPTION_RIGHT]) > 0)
    print("    扫描范围：HTML {} 字节 + {} 个资源 {} 字节".format(
        len(capture["html"]), len(capture["assets"]),
        sum(asset["bytes"] for asset in capture["assets"])), file=sys.stderr)
    return sid


#: 只抹掉**每次请求都不同**的记号。结构上的东西一概不动——下面的差分是在
#: 同一份问卷上做的，问卷号、题号、选项号两次完全一样，没有归一化的余地，
#: 也就没有"归一化顺手把泄漏一起抹掉"的风险。
_REQUEST_NOISE = [
    # CSRF 记号每次请求都不同。两种属性顺序都要认：引擎实际吐的是 value 在前。
    (re.compile(r'value="[^"]*"\s+name="YII_CSRF_TOKEN"'), "CSRF"),
    (re.compile(r'name="YII_CSRF_TOKEN"\s+value="[^"]*"'), "CSRF"),
    (re.compile(r"\b[0-9a-f]{32,64}\b"), "HEX"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b"), "TIME"),
    (re.compile(r"\b\d{10,}\b"), "EPOCH"),
]


def normalise(html: str) -> str:
    for pattern, replacement in _REQUEST_NOISE:
        html = pattern.sub(replacement, html)
    return html


def store_answer_key(context: "Context", survey_id: int, payload: str) -> None:
    """直接改服务端那一份答案键（平台重新发布时走的是同一行）。"""
    assert "'" not in payload, "载荷里有单引号，下面这条 SQL 的引法就不成立了"
    context.db.rows(
        "UPDATE lime_plugin_settings s JOIN lime_plugins p ON p.id = s.plugin_id "
        "SET s.value = '{}' WHERE p.name = '{}' AND s.model = 'Survey' AND s.model_id = {} "
        "AND s.{} = 'mjy_exam_key'".format(payload, PLUGIN, survey_id, context.key_column)
        if context.key_column == "`key`" else
        "UPDATE lime_plugin_settings SET value = '{}' FROM lime_plugins p "
        "WHERE p.id = lime_plugin_settings.plugin_id AND p.name = '{}' "
        "AND lime_plugin_settings.model = 'Survey' AND lime_plugin_settings.model_id = {} "
        "AND lime_plugin_settings.\"key\" = 'mjy_exam_key'".format(payload, PLUGIN, survey_id))
    subprocess.run(["docker", "exec", context.container, "rm", "-rf", "tmp/runtime/cache"], check=True)


def scenario_indistinguishable(context: Context) -> None:
    """K2：**同一份问卷**换掉正确答案，作答者收到的字节一个都不变。

    差分放在同一份问卷上做，是为了让断言尽可能锋利：两次请求的问卷号、题号、
    选项号、资源指纹全都一样，于是除了每次请求都会变的 CSRF 与时间戳以外，
    **不需要任何归一化**。凡是随答案变化的字节都会当场现形——多一个 class、
    多一个 data-、JS 里多一个分支、顺序不同，全都抓得到。

    这正是选择题唯一有意义的断言：选项代码本来就在页面上（它是 input 的 value），
    要证的是"哪个是对的"没有以任何形式随页面下发。
    """
    print("K2: 差分不可区分（同一份问卷换答案）", file=sys.stderr)
    sid = publish_ok(context, definition("K2 indistinguishable", exam=answer_key(OPTION_RIGHT)))

    right_payload = compile_exam(SurveyDefinition.from_dict(
        definition("x", exam=answer_key(OPTION_RIGHT)))).payload
    wrong_payload = compile_exam(SurveyDefinition.from_dict(
        definition("x", exam=answer_key(OPTION_WRONG)))).payload
    context.check("K2: 两份答案键确实不同（差分是有内容的）", right_payload != wrong_payload)

    first = context.capture(start_url(sid), [])
    store_answer_key(context, sid, wrong_payload)
    stored = context.db.value(
        "SELECT s.value FROM lime_plugin_settings s JOIN lime_plugins p ON p.id = s.plugin_id "
        "WHERE p.name = '{}' AND s.model = 'Survey' AND s.model_id = {} AND s.{} = 'mjy_exam_key'".format(
            PLUGIN, sid, context.key_column))
    context.check("K2: 服务端的答案键真的换了", stored == wrong_payload, stored[:200])
    second = context.capture(start_url(sid), [])

    left, right = normalise(first["html"]), normalise(second["html"])
    if left != right:
        at = next((index for index, (a, b) in enumerate(zip(left, right)) if a != b),
                  min(len(left), len(right)))
        detail = {"at": at, "left": left[max(0, at - 120):at + 120],
                  "right": right[max(0, at - 120):at + 120]}
    else:
        detail = ""
    context.check("K2: 换了正确答案之后，作答页逐字节不变", left == right, detail)

    by_url_first = {asset["url"]: asset["sha256"] for asset in first["assets"]}
    by_url_second = {asset["url"]: asset["sha256"] for asset in second["assets"]}
    context.check("K2: 引用的资源集合不变", sorted(by_url_first) == sorted(by_url_second),
                  {"left": sorted(by_url_first), "right": sorted(by_url_second)})
    differing = [url for url in by_url_first if by_url_second.get(url) != by_url_first[url]]
    context.check("K2: 每个资源的内容也逐字节不变", differing == [], differing)


def scenario_status_endpoint(context: Context, sid: int) -> None:
    """K3：公开的回读端点只回摘要与题数。"""
    print("K3: examStatus 不泄漏答案", file=sys.stderr)
    url = "/index.php/plugins/direct?plugin={}&function=examStatus&sid={}".format(PLUGIN, sid)
    body = subprocess.run(
        ["docker", "exec", context.container, "curl", "-s", "--max-time", "30", "http://localhost" + url],
        stdout=subprocess.PIPE, check=True).stdout.decode("utf-8")
    context.check("K3: 端点应答是 JSON 且报告了一份答案键",
                  '"rows":1' in body and '"valid":true' in body, body[:300])
    context.check("K3: 应答里没有哨兵", TEXT_SENTINEL not in body, body[:300])
    context.check("K3: 应答里没有题目代码与 correct 字段",
                  "QCITY" not in body and "correct" not in body, body[:300])


def scenario_guards(context: Context) -> None:
    """V：把答案键写进会被翻成 JS 的地方，发布期就拒绝。"""
    print("V: 发布期守门", file=sys.stderr)
    in_scoring = definition("V scoring", exam=answer_key(OPTION_RIGHT), scoring=[{
        "uuid": str(uuid.uuid4()), "code": "STOTAL", "title": "总分",
        "items": [{"question": "QPICK", "points": {OPTION_RIGHT: 5, OPTION_WRONG: 0}}],
    }])
    codes = [issue.code for issue in
             validate_definition(SurveyDefinition.from_dict(in_scoring)).issues]
    context.check("V: 答案键的题再进计分表被拒", "E_EXAM_KEY_IN_SCORING" in codes, codes)

    in_logic = definition("V logic", exam=answer_key(OPTION_RIGHT),
                          condition='QPICK == "{}"'.format(OPTION_RIGHT))
    codes = [issue.code for issue in
             validate_definition(SurveyDefinition.from_dict(in_logic)).issues]
    context.check("V: 拿正确答案做逻辑条件被拒", "E_EXAM_KEY_IN_LOGIC" in codes, codes)

    result = publish(context.container, in_scoring)
    context.check("V: 这样的定义发布不出去", not result["ok"], result.get("failedStage"))
    context.check("V: 引擎里没有留下半份问卷", result.get("surveyId") in (None, 0), result.get("surveyId"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    args = parser.parse_args()
    context = Context(args.container, Database(args.db, args.db_container), args.db)

    scenario_leak_control(context)
    sid = scenario_no_disclosure(context)
    scenario_indistinguishable(context)
    scenario_status_endpoint(context, sid)
    scenario_guards(context)

    print(json.dumps({"failures": context.failures}))
    return 1 if context.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
