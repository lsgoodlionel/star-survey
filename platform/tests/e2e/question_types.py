#!/usr/bin/env python3

"""WP-02 题型批次（02.1–02.2）真引擎端到端，在宿主机运行，见 platform/deploy/test/run-question-types.sh。

1. **发布**：platform/tests/fixtures/surveys/question-types.json（本批每个题型各一道）走网关七阶段，
   回读校验覆盖每一列（含矩阵的「行_列」、排序的虚列、上传的计数列），引擎静态检查
   （logic_engine_check.php）解析编译出的每条服务端规则。
2. **完整作答**（HTTP，与浏览器一致）：逐列断言答卷表里的值，再经网关读端点
   （ResponseReadService → export_responses）逐列读回。
3. **空答**：只答必答题，逐列记录「没作答」在库里是 "" 还是 NULL（映射表第四节）。
4. **篡改**：每条一个新会话，前几页正常作答，在目标页提交非法值并篡改影子字段
   （``relevance*`` 全部写 1 或把目标题写 0、``java*`` 写成合法值）。断言引擎把人留在本页，
   且没有任何已提交答卷带着这个值。
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from publish_gateway_logic import docker_php  # noqa: E402
from pubgw.auth import sign  # noqa: E402
from pubgw.engines import EngineConfig  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.responses import ResponseReadService  # noqa: E402
from pubgw.rpc import RemoteControlClient, RpcError  # noqa: E402
from question_types_plan import MARKERS, PAGES, TAMPERS, blank_pages, valid_pages  # noqa: E402

DEFINITION = REPO_ROOT / "platform/tests/fixtures/surveys/question-types.json"
RESPONDER = "platform/tests/e2e/logic_respond.php"
ENGINE_CHECK = "platform/tests/e2e/logic_engine_check.php"
SECRET = b"e2e-question-types-secret-0123456789abcdef"
RPC_URL = "http://localhost/index.php/admin/remotecontrol"

Column = Tuple[str, str, int]  # (题目代码, aid, 尺度)


class Run:
    def __init__(self, container: str, db: Database, driver: str, survey_id: int, binding: Dict[str, Any]):
        self.container = container
        self.db = db
        self.driver = driver
        self.survey_id = survey_id
        self.fields: Dict[Column, str] = {}
        for question in binding["questions"]:
            for field in question["fields"]:
                self.fields[(question["code"], field["aid"], field["scale"])] = field["fieldname"]
        self.qids = {title: qid for title, qid in db.rows(
            "SELECT title, qid FROM lime_questions WHERE sid = {} AND parent_qid = 0".format(survey_id))}
        table = "lime_responses_{}".format(survey_id)
        self.table = table
        self.physical = {row[0] for row in db.rows(
            "SELECT column_name FROM information_schema.columns WHERE table_name = '{}'".format(table))}
        self.failures: List[str] = []
        self.report: Dict[str, Any] = {}

    def check(self, label: str, condition: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if condition else "FAIL", label, "" if condition else ": {}".format(detail)),
              file=sys.stderr)
        if not condition:
            self.failures.append(label)

    def post(self, answers: Dict[Column, str]) -> Dict[str, str]:
        return {self.fields[column]: value for column, value in answers.items()}

    def respond(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        plan = json.dumps({"steps": steps}).encode("utf-8")
        return docker_php(self.container, RESPONDER, str(self.survey_id), stdin=plan)

    def quote(self, name: str) -> str:
        return '"{}"'.format(name) if self.driver == "pgsql" else "`{}`".format(name)

    def stored_columns(self) -> List[Tuple[Column, str]]:
        return [(column, name) for column, name in self.fields.items() if name in self.physical]

    def last_row(self) -> Dict[Column, Optional[str]]:
        cast = "TEXT" if self.driver == "pgsql" else "CHAR(4000)"
        columns = self.stored_columns()
        select = ", ".join("COALESCE(CAST({} AS {}), '<NULL>')".format(self.quote(name), cast) for _, name in columns)
        rows = self.db.rows("SELECT id, submitdate IS NOT NULL, {} FROM {} ORDER BY id DESC LIMIT 1".format(
            select, self.table))
        row = rows[0] + [""] * (len(columns) + 2 - len(rows[0]))  # psql/mariadb 省略行尾空串
        values: Dict[Column, Optional[str]] = {
            column: (None if value == "<NULL>" else value) for (column, _), value in zip(columns, row[2:])
        }
        values[("_id", "", 0)] = row[0]
        values[("_submitted", "", 0)] = "1" if row[1] in ("1", "t", "true") else "0"
        return values

    def max_id(self) -> int:
        return int(self.db.value("SELECT COALESCE(MAX(id), 0) FROM {}".format(self.table)) or 0)

    def submitted_since(self, last_id: int) -> int:
        return int(self.db.value("SELECT COUNT(*) FROM {} WHERE id > {} AND submitdate IS NOT NULL".format(
            self.table, last_id)) or 0)

    def state(self, page: Dict[str, Any], code: str) -> str:
        return page["questions"].get(self.qids[code], "absent")


def same(expected: str, actual: Optional[str]) -> bool:
    if actual is None:
        return False
    if expected == actual:
        return True
    try:
        return float(expected) == float(actual)
    except ValueError:
        return actual.startswith(expected) and expected[:4].isdigit()  # 日期列带时分秒


# ------------------------------------------------------------------ 场景


def scenario_complete(run: Run) -> str:
    print("scenario A: every batch type answered validly", file=sys.stderr)
    pages = valid_pages()
    steps = [{"answers": run.post(answers), "move": "movenext"} for answers in pages]
    steps[-1]["move"] = "movesubmit"
    result = run.respond(steps)
    run.check("A: completed", result["completed"] is True, result["pages"][-1])
    row = run.last_row()
    run.check("A: response submitted", row[("_submitted", "", 0)] == "1", row)
    expected = {column: value for answers in pages for column, value in answers.items()}
    for column, value in sorted(expected.items()):
        if run.fields[column] not in run.physical:
            continue
        run.check("A: {}[{}#{}] stored {!r}".format(*column, value), same(value, row.get(column)), row.get(column))
    rank = row[("QRANK", "", 0)] or ""
    run.check("A: ranking stored as JSON in rank order", json.loads(rank or "null") == ["I3", "I1", "I2"], rank)
    run.check("A: unchecked multiple-choice option stored as ''", row[("QMULTI", "S3", 0)] == "", row[("QMULTI", "S3", 0)])
    run.report["complete"] = {"{}[{}#{}]".format(*column): value for column, value in row.items()}
    return row[("_id", "", 0)]


def scenario_blank(run: Run) -> None:
    print("scenario B: only mandatory questions answered — how the engine stores 'not answered'", file=sys.stderr)
    pages = blank_pages()
    steps = [{"answers": run.post(answers), "move": "movenext"} for answers in pages]
    steps[-1]["move"] = "movesubmit"
    result = run.respond(steps)
    run.check("B: completed with only the mandatory answers", result["completed"] is True, result["pages"][-1])
    row = run.last_row()
    missing = {"{}[{}#{}]".format(*column): ("NULL" if value is None else repr(value))
               for column, value in row.items() if not column[0].startswith("_")}
    run.report["blank"] = missing
    for code in ("QDROP", "QSHORT", "QARR", "QMTXT"):
        cells = [value for (qcode, _, _), value in row.items() if qcode == code]
        run.check("B: unanswered {} stored as ''".format(code), cells and all(v == "" for v in cells), cells)
    for code in ("QNUM", "QDATE", "QALLOC"):
        cells = [value for (qcode, _, _), value in row.items() if qcode == code]
        run.check("B: unanswered {} stored as NULL".format(code), cells and all(v is None for v in cells), cells)


def scenario_tampers(run: Run) -> None:
    print("scenario C: tampered submissions — rejected on the page, or corrected before storage", file=sys.stderr)
    pages = valid_pages()
    for tamper in TAMPERS:
        page_index = tamper["page"]
        steps = [{"answers": run.post(answers), "move": "movenext"} for answers in pages[:page_index]]
        answers = dict(pages[page_index])
        answers.update(tamper["answers"])
        posted = run.post(answers)
        posted.update(shadow_fields(run, tamper, posted))
        move = tamper.get("move") or ("movesubmit" if page_index == len(PAGES) - 1 else "movenext")
        steps.append({"answers": posted, "move": move})
        if tamper.get("expect") == "normalize":
            later = [{"answers": run.post(page), "move": "movenext"} for page in pages[page_index + 1:]]
            later[-1]["move"] = "movesubmit"
            steps.extend(later)
        before = run.max_id()
        result = run.respond(steps)
        label = "C {}".format(tamper["label"])
        if tamper.get("expect") == "normalize":
            row = run.last_row()
            run.check(label + ": completed", result["completed"] is True, result["pages"][-1])
            for column, value in tamper["stored"].items():
                got = row.get(column)
                ok = got in (None, "") if value is None else got == value
                run.check("{}: stored {}[{}#{}] = {!r}".format(label, *column, value), ok, got)
            continue
        after = result["pages"][-1]
        marker = tamper.get("marker", MARKERS[page_index])
        held = not result["completed"] and (marker is None or run.state(after, marker) == "visible")
        where = "on page {}".format(page_index + 1) if marker else "short of completion"
        run.check("{}: rejected, respondent kept {}".format(label, where), held, after)
        run.check(label + ": no submitted response from this session", run.submitted_since(before) == 0)


def shadow_fields(run: Run, tamper: Dict[str, Any], posted: Dict[str, str]) -> Dict[str, str]:
    """篡改浏览器端的影子字段：relevance* 表示前端算出的相关性，java* 是前端保存的值副本。"""
    shadow: Dict[str, str] = {}
    mode = tamper.get("shadow")
    if mode == "all-relevant":
        for qid in run.qids.values():
            shadow["relevance" + qid] = "1"
        for name in posted:
            shadow["relevance" + name] = "1"
    elif mode == "claim-hidden":
        for column in tamper["answers"]:
            shadow["relevance" + run.qids[column[0]]] = "0"
    elif mode == "java-valid":
        for column, value in tamper.get("java", {}).items():
            shadow["java" + run.fields[column]] = value
    return shadow


def scenario_response_read(run: Run, response_id: str, instance: str) -> None:
    print("scenario D: gateway response read returns every stored column", file=sys.stderr)
    names = [name for _, name in run.stored_columns()]
    virtual = [name for column, name in run.fields.items() if name not in run.physical]
    engine = EngineConfig(instance, RPC_URL, ADMIN_USER, ADMIN_PASSWORD)
    service = ResponseReadService(engines={instance: engine}, secret=SECRET,
                                  transport_factory=lambda cfg: DockerCurlTransport(run.container))
    reply = read_responses(service, instance, run.survey_id, int(response_id), names)
    run.check("D: HTTP 200 for every physical column", reply["status"] == 200, reply)
    values = (reply["body"].get("responses") or [{}])[0].get("values", {})
    row = run.report["complete"]
    by_name = {name: column for column, name in run.fields.items()}
    for name in names:
        column = by_name[name]
        key = "{}[{}#{}]".format(*column)
        db_value = row.get(key)
        got = values.get(name)
        ok = (db_value is None and got in (None, "")) or (db_value is not None and got is not None and same(db_value, got))
        run.check("D: {} read back as stored".format(key), ok, {"db": db_value, "read": got})
    if virtual:
        scenario_rank_columns(run, service, instance, response_id, names, virtual)


def scenario_rank_columns(run: Run, service: ResponseReadService, instance: str, response_id: str,
                          names: List[str], virtual: List[str]) -> None:
    """排序题的名次列没有物理列，值由网关从主列 JSON 摊出来（question-type-map.md 第四节）。"""
    print("scenario D2: virtual rank columns carry the item ranked at that position", file=sys.stderr)
    reply = read_responses(service, instance, run.survey_id, int(response_id), names + virtual)
    run.check("D2: HTTP 200 with the virtual rank columns too", reply["status"] == 200, reply)
    values = (reply["body"].get("responses") or [{}])[0].get("values", {})
    for column, name in sorted(run.fields.items()):
        if name not in virtual:
            continue
        code, aid, _ = column
        if not aid.isdigit():
            run.check("D2: {} is a rank column".format(name), False, column)
            continue
        ranked = json.loads(run.report["complete"].get("{}[#0]".format(code)) or "[]")
        position = int(aid) - 1
        expected = ranked[position] if 0 <= position < len(ranked) else ""
        run.check("D2: {} rank {} reads back as {!r}".format(code, aid, expected),
                  values.get(name) == expected, {"read": values.get(name), "stored": ranked})
    run.report["readWithVirtualRankColumns"] = {
        "status": reply["status"], "values": {name: values.get(name) for name in virtual}}


def read_responses(service: ResponseReadService, instance: str, survey_id: int, response_id: int,
                   fields: List[str]) -> Dict[str, Any]:
    body = json.dumps({"engineInstanceId": instance, "surveyId": survey_id,
                       "responseIds": [response_id], "fields": fields}).encode("utf-8")
    stamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-Pubgw-Timestamp": stamp,
               "X-Pubgw-Signature": sign(SECRET, stamp, body)}
    response = service.read(headers, body)
    return {"status": response.status, "body": json.loads(response.body.decode("utf-8"))}


# ------------------------------------------------------------------ 主流程


def parse_only_regex_artifact(error: Dict[str, Any]) -> bool:
    """解析模式下引擎把函数实参一律当成 1，``geterrors_exprmgr_regexMatch`` 于是拿 "1" 当正则报错
    （em_core_helper.php RDP_RunFunction）。引擎自带的 preg 属性走同一条路，同样会被报。
    这条只忽略「正则无效」这一种、且整条表达式没有别的错误；正则本身是否正确由场景 A（合法值
    被接受）与场景 C（非法值被拒）在真实求值下证明。"""
    messages = [message for message in error["errors"] if message != "Not a valid expression"]
    return bool(messages) and all(message == "Invalid PERL Regular Expression: 1 at regexMatch" for message in messages)


def publish(client: RemoteControlClient, instance: str) -> Dict[str, Any]:
    definition = SurveyDefinition.from_json(DEFINITION.read_text(encoding="utf-8"))
    result = Publisher(client, engine_instance=instance).publish(definition)
    if not result.ok:
        raise RuntimeError("publish failed at {}: {}".format(result.failed_stage, result.failures))
    return result.to_dict()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="survey-test-web")
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    parser.add_argument("--keep", action="store_true", help="leave the published survey in the engine")
    args = parser.parse_args()

    client = RemoteControlClient(DockerCurlTransport(args.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    survey_id = None
    try:
        published = publish(client, args.container)
        survey_id = published["surveyId"]
        print("published survey {} ({})".format(survey_id, published["binding"]["fingerprint"]), file=sys.stderr)
        run = Run(args.container, Database(args.db, args.db_container), args.db, survey_id, published["binding"])
        run.check("publish: every compiled column bound", all(
            question["fields"] for question in published["binding"]["questions"]))

        report = docker_php(args.container, ENGINE_CHECK, str(survey_id))
        errors = [error for error in report["errors"] if not parse_only_regex_artifact(error)]
        run.check("engine parses all {} compiled expressions".format(report["checked"]),
                  report["checked"] > 0 and not errors, errors)

        response_id = scenario_complete(run)
        scenario_response_read(run, response_id, args.container)
        scenario_blank(run)
        scenario_tampers(run)

        print(json.dumps({"database": args.db, "surveyId": survey_id, "failures": run.failures,
                          "blank": run.report.get("blank"),
                          "readWithVirtualRankColumns": run.report.get("readWithVirtualRankColumns")},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if run.failures else 0
    finally:
        if survey_id is not None and not args.keep:
            try:
                client.delete_survey(survey_id)
            except RpcError as error:
                print("cleanup of survey {} failed: {}".format(survey_id, error), file=sys.stderr)
        client.logout()


if __name__ == "__main__":
    raise SystemExit(main())
