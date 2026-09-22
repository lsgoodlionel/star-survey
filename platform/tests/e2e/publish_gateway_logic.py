#!/usr/bin/env python3

"""WP-03 逻辑端到端：v2 定义（条件＋校验＋计算值＋引用）经网关发布到真引擎，再验证引擎的行为。

在宿主机运行，见 platform/deploy/test/run-publish-gateway-logic.sh。

1. **发布**：platform/tests/fixtures/surveys/publish-gateway-logic.json 走完整七阶段。
2. **引擎静态检查**：logic_engine_check.php 用引擎自己的 ExpressionManager 解析
   问卷里每一条 relevance / 校验 / 计算公式 / 文本替换，要求零错误、零未知变量。
3. **真实作答**（logic_respond.php，HTTP，与浏览器一致），三条路径：
   A 猫：养狗才显示的必答题被隐藏且不挡提交；校验规则挡住 99 年；计算值写库；
     用户输入的 HTML 被转义显示；第三页条件成立。
   B 先选狗并答了狗名，退回第一页改成猫：狗名必须被清空（隐藏必答与清值一致）。
   C 没有宠物：整个第二组被题组条件隐藏，组内计算值为空，第三页条件不成立。
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402

DEFINITION = REPO_ROOT / "platform/tests/fixtures/surveys/publish-gateway-logic.json"
RESPONDER = "platform/tests/e2e/logic_respond.php"
ENGINE_CHECK = "platform/tests/e2e/logic_engine_check.php"
HOSTILE_NAME = "<b>Tom</b>{QAGE}"
ESCAPED_NAME = "&lt;b&gt;Tom&lt;/b&gt;&#123;QAGE&#125;"


class Context:
    def __init__(self, container: str, db: Database, survey_id: int, fields: Dict[str, str], qids: Dict[str, str]):
        self.container = container
        self.db = db
        self.survey_id = survey_id
        self.fields = fields
        self.qids = qids
        self.failures: List[str] = []

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label, "" if condition else ": {}".format(detail)),
              file=sys.stderr)
        if not condition:
            self.failures.append(label)

    def answers(self, **values: str) -> Dict[str, str]:
        return {self.fields[code]: value for code, value in values.items()}

    def state(self, page: Dict[str, Any], code: str) -> str:
        return page["questions"].get(self.qids[code], "absent")


def docker_php(container: str, script: str, *args: str, stdin: bytes = b"") -> Dict[str, Any]:
    completed = subprocess.run(
        ["docker", "exec", "-i", container, "php", script, *args],
        input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError("{} failed: {}{}".format(script, completed.stderr.decode(), completed.stdout.decode()))
    return json.loads(completed.stdout.decode("utf-8"))


def respond(context: Context, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    plan = json.dumps({"steps": steps}).encode("utf-8")
    return docker_php(context.container, RESPONDER, str(context.survey_id), stdin=plan)


def last_response(context: Context) -> Dict[str, str]:
    table = "lime_responses_{}".format(context.survey_id)
    columns = list(context.fields.items())
    quote = (lambda name: '"{}"'.format(name)) if context.db._driver == "pgsql" else (lambda name: "`{}`".format(name))
    select = ", ".join("COALESCE(CAST({} AS CHAR(200)), '<NULL>')".format(quote(field)) for _, field in columns)
    if context.db._driver == "pgsql":
        select = select.replace("AS CHAR(200)", "AS TEXT")
    rows = context.db.rows(
        "SELECT submitdate IS NOT NULL, {} FROM {} ORDER BY id DESC LIMIT 1".format(select, table)
    )
    row = rows[0]
    values = {code: (None if value == "<NULL>" else value) for (code, _), value in zip(columns, row[1:])}
    values["_submitted"] = row[0] in ("1", "t", "true")
    return values


class NamedDatabase(Database):
    """同 publish_gateway.Database，但数据库容器名可配（隔离栈用）。"""

    def __init__(self, driver: str, container: str):
        super().__init__(driver)
        self._container = container

    def rows(self, sql: str) -> List[List[str]]:
        if self._driver == "pgsql":
            command = ["docker", "exec", self._container, "psql", "-U", "postgres",
                       "-d", "limesurvey", "-tA", "-F", "\t", "-c", sql]
        else:
            command = ["docker", "exec", self._container, "mariadb", "-uroot", "-proot",
                       "-N", "-B", "limesurvey", "-e", sql]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            raise RuntimeError("sql failed: " + completed.stderr.decode("utf-8", "replace"))
        text = completed.stdout.decode("utf-8").strip()
        return [line.split("\t") for line in text.splitlines() if line]


def number(value: Any) -> float:
    return float(value) if value not in (None, "") else float("nan")


# ------------------------------------------------------------------ 场景


def scenario_cat(context: Context) -> None:
    print("scenario A: cat — hidden-and-required does not block, validation blocks, calculation stored",
          file=sys.stderr)
    result = respond(context, [
        {"answers": context.answers(QAGE="36", QPET="A1", QNAME=HOSTILE_NAME), "move": "movenext"},
        {"answers": context.answers(QYEARS="99"), "move": "movenext", "needles": [ESCAPED_NAME, HOSTILE_NAME]},
        {"answers": context.answers(QYEARS="5"), "move": "movenext"},
        {"answers": context.answers(QFEED="fine"), "move": "movesubmit"},
    ])
    pages = result["pages"]
    context.check("A page 2: mandatory dog-name question is hidden", context.state(pages[1], "QDOGNAME") == "hidden",
                  pages[1])
    context.check("A page 2: years question is shown", context.state(pages[1], "QYEARS") == "visible", pages[1])
    context.check("A page 2: piped nickname is HTML-escaped", pages[1]["needles"].get(ESCAPED_NAME) is True)
    context.check("A page 2: raw nickname markup never reaches the page",
                  pages[1]["needles"].get(HOSTILE_NAME) is False)
    context.check("A: 99 years > age 36 is rejected (page 2 shown again)",
                  context.state(pages[2], "QYEARS") == "visible", pages[2])
    context.check("A page 3: wrap-up question shown (score 46 > 40)", context.state(pages[3], "QFEED") == "visible",
                  pages[3])
    context.check("A: completed", result["completed"] is True)
    row = last_response(context)
    context.check("A: response submitted", row["_submitted"] is True, row)
    context.check("A: hidden dog name stored as NULL", row["QDOGNAME"] is None, row)
    context.check("A: years stored", number(row["QYEARS"]) == 5, row)
    context.check("A: calculated score = 36 + 5*2", number(row["QSCORE"]) == 46, row)
    context.check("A: text calculation stored escaped", row["QGREET"] is not None and "<b>" not in row["QGREET"]
                  and row["QGREET"].startswith("Hi "), row)
    context.check("A: wrap-up answer stored", row["QFEED"] == "fine", row)


def scenario_dog_then_cat(context: Context) -> None:
    print("scenario B: dog name answered, then pet changed to cat — the hidden answer is cleared", file=sys.stderr)
    result = respond(context, [
        {"answers": context.answers(QAGE="30", QPET="A2", QNAME="Ann"), "move": "movenext"},
        {"answers": context.answers(QYEARS="2"), "move": "movenext"},
        {"answers": context.answers(QDOGNAME="Rex", QYEARS="2"), "move": "moveprev"},
        {"answers": context.answers(QPET="A1"), "move": "movenext"},
        {"answers": {}, "move": "movenext"},
        {"answers": {}, "move": "movesubmit"},
    ])
    pages = result["pages"]
    context.check("B page 2: dog-name question shown for a dog", context.state(pages[1], "QDOGNAME") == "visible",
                  pages[1])
    context.check("B: empty mandatory dog name blocks (page 2 shown again)",
                  context.state(pages[2], "QDOGNAME") == "visible", pages[2])
    context.check("B: back on page 1", context.state(pages[3], "QPET") == "visible", pages[3])
    context.check("B page 2 again: dog-name question hidden after switching to cat",
                  context.state(pages[4], "QDOGNAME") == "hidden", pages[4])
    context.check("B: wrap-up never shown (score 34 <= 40)",
                  all(context.state(page, "QFEED") != "visible" for page in pages), pages[4:])
    context.check("B: completed", result["completed"] is True)
    row = last_response(context)
    context.check("B: previously answered dog name cleared to NULL", row["QDOGNAME"] is None, row)
    context.check("B: calculated score = 30 + 2*2", number(row["QSCORE"]) == 34, row)
    context.check("B: hidden wrap-up stored as NULL", row["QFEED"] is None, row)


def scenario_no_pet(context: Context) -> None:
    print("scenario C: no pet — the whole pet group is hidden by its group condition", file=sys.stderr)
    result = respond(context, [
        {"answers": context.answers(QAGE="50", QPET="A3", QNAME="Zed"), "move": "movenext"},
        {"answers": {}, "move": "movesubmit"},
    ])
    pages = result["pages"]
    context.check("C: the pet group is skipped", "absent" == context.state(pages[1], "QYEARS")
                  or context.state(pages[1], "QYEARS") == "hidden", pages[1])
    context.check("C: completed", result["completed"] is True, pages)
    row = last_response(context)
    context.check("C: response submitted", row["_submitted"] is True, row)
    context.check("C: calculated score in the hidden group is NULL", row["QSCORE"] is None, row)
    context.check("C: years in the hidden group is NULL", row["QYEARS"] is None, row)
    context.check("C: wrap-up stored as NULL", row["QFEED"] is None, row)


# ------------------------------------------------------------------ 主流程


def publish(container: str) -> Dict[str, Any]:
    definition = SurveyDefinition.from_json(DEFINITION.read_text(encoding="utf-8"))
    client = RemoteControlClient(DockerCurlTransport(container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        result = Publisher(client, engine_instance=container).publish(definition)
    finally:
        client.logout()
    if not result.ok:
        raise RuntimeError("publish failed at {}: {}".format(result.failed_stage, result.failures))
    return result.to_dict()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="survey-test-web")
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", default=None,
                        help="database container (default survey-test-db / survey-test-pg)")
    args = parser.parse_args()
    db_container = args.db_container or ("survey-test-pg" if args.db == "pgsql" else "survey-test-db")

    published = publish(args.container)
    survey_id = published["surveyId"]
    print("published survey {} ({})".format(survey_id, published["binding"]["fingerprint"]), file=sys.stderr)
    fields = {
        question["code"]: question["fields"][0]["fieldname"]
        for question in published["binding"]["questions"]
        if question["fields"]
    }
    db = NamedDatabase(args.db, db_container)
    qids = {
        title: qid
        for title, qid in db.rows(
            "SELECT title, qid FROM lime_questions WHERE sid = {} AND parent_qid = 0".format(survey_id)
        )
    }
    context = Context(args.container, db, survey_id, fields, qids)

    report = docker_php(args.container, ENGINE_CHECK, str(survey_id))
    context.check(
        "engine ExpressionManager parses all {} compiled expressions without errors".format(report["checked"]),
        report["checked"] > 0 and not report["errors"],
        report["errors"],
    )
    context.check("engine check catches an unknown variable and a syntax error (negative control)",
                  all(report["canariesCaught"].values()), report["canariesCaught"])

    scenario_cat(context)
    scenario_dog_then_cat(context)
    scenario_no_pet(context)

    print(json.dumps({"surveyId": survey_id, "failures": context.failures}))
    return 1 if context.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
