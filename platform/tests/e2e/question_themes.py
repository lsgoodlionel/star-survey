#!/usr/bin/env python3

"""WP-02 切片 02.3 真引擎端到端：新题型主题与副表题型，见 platform/deploy/test/run-question-themes.sh。

1. **装主题 → 发布**：平台自带的 mjy-* 主题先装进 lime_question_themes（不装就会静默降级），
   再把 platform/tests/fixtures/surveys/question-themes.json 走网关七阶段发布，
   回读确认每道题的 question_theme_name 没有被换掉。
2. **冒烟渲染**：真实 HTTP 打开每一页，断言每个主题的标记与它的 JS 都真的出现在页面上
   （ADR 0006 限制 6：题型主题的 twig 越界只在作答时才暴露）。
3. **完整作答**：含特殊字符与取上限的值；逐列断言答卷表，副表逐格断言，并核对结构版本；
   再经网关读端点把每一列读回来。
4. **只答必答题**：其余列在库里是 "" 还是 NULL，逐列记录。
5. **篡改**：每条一个新会话，在目标页提交非法值（必要时把 relevance* 影子字段全写 1），
   断言引擎/插件把人留在本页，且没有任何已提交答卷带着这个值。
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
from question_themes_plan import (  # noqa: E402
    BOILERPLATE_TAMPER, MARKERS, PAGES, TAMPERS, THEME_MARKERS, blank_pages, valid_pages,
)

DEFINITION = REPO_ROOT / "platform/tests/fixtures/surveys/question-themes.json"
RESPONDER = "platform/tests/e2e/logic_respond.php"
THEME_INSTALLER = "platform/tests/e2e/install_question_themes.php"
SECRET = b"e2e-question-themes-secret-0123456789abcd"
RPC_URL = "http://localhost/index.php/admin/remotecontrol"

CELL_TABLE = "lime_mjyquestionextensions_answer_cell"
STATE_TABLE = "lime_mjyquestionextensions_answer_state"

Column = Tuple[str, str, int]


class Run:
    def __init__(self, container: str, db: Database, driver: str, survey_id: int, binding: Dict[str, Any]):
        self.container = container
        self.db = db
        self.driver = driver
        self.survey_id = survey_id
        self.fields: Dict[Column, str] = {}
        self.side_tables: Dict[str, Dict[str, Any]] = {}
        for question in binding["questions"]:
            for field in question["fields"]:
                self.fields[(question["code"], field["aid"], field["scale"])] = field["fieldname"]
            if question.get("sideTable"):
                self.side_tables[question["code"]] = question["sideTable"]
        self.qids = {title: qid for title, qid in db.rows(
            "SELECT title, qid FROM lime_questions WHERE sid = {} AND parent_qid = 0".format(survey_id))}
        self.table = "lime_responses_{}".format(survey_id)
        self.physical = {row[0] for row in db.rows(
            "SELECT column_name FROM information_schema.columns WHERE table_name = '{}'".format(self.table))}
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

    # --------------------------------------------------------- 副表

    def side_rows(self, response_id: int, code: str) -> List[Dict[str, str]]:
        rows = self.db.rows(
            "SELECT row_index, column_code, cell_value FROM {} WHERE survey_id = {} AND response_id = {}"
            " AND question_code = '{}' ORDER BY row_index, column_code".format(
                CELL_TABLE, self.survey_id, response_id, code))
        grouped: Dict[int, Dict[str, str]] = {}
        for row_index, column_code, value in ((int(r[0]), r[1], r[2] if len(r) > 2 else "") for r in rows):
            grouped.setdefault(row_index, {})[column_code] = value
        return [grouped[index] for index in sorted(grouped)]

    def side_state(self, response_id: int, code: str) -> Dict[str, str]:
        rows = self.db.rows(
            "SELECT structure_version, row_count, is_valid FROM {} WHERE survey_id = {} AND response_id = {}"
            " AND question_code = '{}'".format(STATE_TABLE, self.survey_id, response_id, code))
        if not rows:
            return {}
        return {"structureVersion": rows[0][0], "rowCount": rows[0][1], "isValid": rows[0][2]}


# ------------------------------------------------------------------ 场景


def scenario_render(run: Run) -> None:
    """冒烟渲染：每个主题的标记与它的 JS 必须真的出现在作答页上。"""
    print("scenario A: every new theme renders on the real survey page", file=sys.stderr)
    pages = valid_pages()
    steps = [{"answers": run.post(answers), "move": "movenext", "needles": list(THEME_MARKERS[index])}
             for index, answers in enumerate(pages)]
    steps[-1]["move"] = "movesubmit"
    result = run.respond(steps)
    for index, page_name in enumerate(PAGES):
        found = result["pages"][index]["needles"]
        for needle in THEME_MARKERS[index]:
            run.check("A: {} renders {}".format(page_name, needle), found.get(needle) is True, found)
    run.check("A: the survey can be completed with the themed questions", result["completed"] is True,
              result["pages"][-1])
    return run.last_row()[("_id", "", 0)]


def scenario_values(run: Run, response_id: str) -> None:
    """逐列断言答卷表，再逐格断言副表（含结构版本）。"""
    print("scenario B: every stored column and every projected cell", file=sys.stderr)
    from question_themes_plan import (
        HEAT_ROWS, KANO_ROWS, LOOP_ROWS, MARK_ROWS, MARK_SEGMENTS, PK_ROWS, PSYCH_ROWS,
        SHELF_ROWS, TABLE_ROWS,
    )

    #: 走副表的题：(题目代码, 归一化后的行, 结构版本)。
    side = (("QTABLE", TABLE_ROWS, "rt1"), ("QLOOP", LOOP_ROWS, "lr1"),
            ("QPK", PK_ROWS, "pk1"), ("QSHELF", SHELF_ROWS, "sh1"),
            ("QMARK", MARK_ROWS, "th1"), ("QPSY", PSYCH_ROWS, "ps1"),
            ("QKANO", KANO_ROWS, "kn1"), ("QHEAT", HEAT_ROWS, "hm1"))
    row = run.last_row()
    run.check("B: response submitted", row[("_submitted", "", 0)] == "1", row)
    expected = {column: value for answers in valid_pages() for column, value in answers.items()}
    for column, value in sorted(expected.items()):
        if run.fields[column] not in run.physical:
            continue
        if column[0] in {code for code, _rows, _version in side}:
            continue  # 信封另行按 JSON 比对：插件会重新编码它
        run.check("B: {}[{}#{}] stored {!r}".format(*column, value), row.get(column) == value, row.get(column))
    run.check("B: the boilerplate column stays empty", row[("QSEC", "", 0)] == "", row[("QSEC", "", 0)])
    run.check("B: an unchecked option stores ''", row[("QBLANK", "S2", 0)] == "", row[("QBLANK", "S2", 0)])
    # 分组只换展示：没勾的那个子题照样是自己的一列，值是 ""，不受分组重排影响。
    run.check("B: an unchecked grouped multiple-choice option stores ''",
              row[("QGRPM", "M2", 0)] == "", row[("QGRPM", "M2", 0)])
    run.check("B: the blank of an unchecked option stays empty",
              row[("QBLANK", "S2comment", 0)] == "", row[("QBLANK", "S2comment", 0)])

    for code, rows, _version in side:
        stored = json.loads(row[(code, "", 0)] or "null")
        run.check("B: the {} envelope is re-normalised by the plugin".format(code),
                  stored == {"v": 1, "rows": rows}, stored)

    identifier = int(response_id)
    for code, rows, version in side:
        run.check("B: {} projected into the side table".format(code), run.side_rows(identifier, code) == rows,
                  run.side_rows(identifier, code))
        state = run.side_state(identifier, code)
        run.check("B: {} side state records structure version {}".format(code, version),
                  state.get("structureVersion") == version, state)
        run.check("B: {} side state is valid with {} rows".format(code, len(rows)),
                  state.get("isValid") == "1" and state.get("rowCount") == str(len(rows)), state)
        declared = run.side_tables.get(code, {})
        run.check("B: {} binding declares the same structure version".format(code),
                  declared.get("structureVersion") == version, declared)
        run.check("B: {} binding declares the side table contract".format(code),
                  declared.get("contract") == "question-extension-tables-v1"
                  and str(declared.get("structureDigest", "")).startswith("sd1:"), declared)
    # 循环评价的列字典必须带上取值集合：读端靠它把 "1" 翻回「差」，
    # 插件靠同一份集合拒收不在里面的评分（枚举列）。
    loop_columns = run.side_tables.get("QLOOP", {}).get("columns", [])
    run.check("B: QLOOP binding declares the object column as a unique enum",
              loop_columns[:1] and loop_columns[0]["code"] == "target"
              and loop_columns[0]["type"] == "enum" and loop_columns[0].get("distinct") is True
              and [item["code"] for item in loop_columns[0]["options"]] == ["B1", "B2"], loop_columns[:1])
    run.check("B: QLOOP binding declares every dimension over the declared scale",
              [column["code"] for column in loop_columns[1:]] == ["price", "service"]
              and all([item["code"] for item in column["options"]] == ["1", "2", "3"]
                      for column in loop_columns[1:]), loop_columns[1:])
    # 图片 PK：一对一列，这一列的可选值恰好是这一对的两张图——「选了别对的图」
    # 由枚举列自己挡住，不需要任何跨列规则。
    pk_columns = {column["code"]: column for column in run.side_tables.get("QPK", {}).get("columns", [])}
    run.check("B: QPK binding declares one column per pair plus its shown column",
              sorted(pk_columns) == ["P1", "P1_shown", "P2", "P2_shown"], sorted(pk_columns))
    run.check("B: each QPK choice column only allows its own two pictures",
              [item["code"] for item in pk_columns["P1"]["options"]] == ["A", "B"]
              and [item["code"] for item in pk_columns["P2"]["options"]] == ["B", "C"], pk_columns)
    run.check("B: the QPK shown column is not mandatory",
              pk_columns["P1_shown"]["required"] is False, pk_columns["P1_shown"])
    # 货架题：商品列枚举＋唯一（同一件不能取两次，要多拿就改件数），件数列是有界整数。
    shelf_columns = {column["code"]: column for column in run.side_tables.get("QSHELF", {}).get("columns", [])}
    run.check("B: QSHELF binding declares a unique product enum and a bounded quantity",
              [item["code"] for item in shelf_columns.get("product", {}).get("options", [])] == ["S1", "S2", "S3"]
              and shelf_columns["product"].get("distinct") is True
              and (shelf_columns["qty"]["type"], shelf_columns["qty"]["min"],
                   shelf_columns["qty"]["max"]) == ("integer", 1, 9), shelf_columns)
    # 文字点睛：片段列是枚举＋唯一，取值代码里同时带着偏移与那一段原文的指纹——
    # 「中文标记偏移和原文版本一致」在服务端就是这一条（改了原文，代码就变）。
    mark_columns = {column["code"]: column for column in run.side_tables.get("QMARK", {}).get("columns", [])}
    run.check("B: QMARK binding declares a unique span enum and a tag enum",
              [option["code"] for option in mark_columns.get("segment", {}).get("options", [])]
              == list(MARK_SEGMENTS)
              and mark_columns["segment"].get("distinct") is True
              and [option["code"] for option in mark_columns["tag"]["options"]] == ["like", "dislike"],
              mark_columns)
    run.check("B: every QMARK span is labelled with the source text it points at",
              [option["label"] for option in mark_columns["segment"]["options"]]
              == ["苹果很甜", "香蕉太软", "梨子刚好"], mark_columns.get("segment"))
    # 心理实验：试次列枚举＋唯一、按键列枚举、反应时是有界整数，**没有任何一列能放对错**——
    # 正确率由平台按定义里的 trials[].correct 推导，作答者提交不了「我答对了」。
    psych_columns = {column["code"]: column for column in run.side_tables.get("QPSY", {}).get("columns", [])}
    run.check("B: QPSY binding declares trial, key and reaction time and nothing else",
              list(psych_columns) == ["trial", "key", "rt"], list(psych_columns))
    run.check("B: QPSY pins the trial column to the declared trials and forbids repeats",
              [option["code"] for option in psych_columns.get("trial", {}).get("options", [])] == ["T1", "T2"]
              and psych_columns["trial"].get("distinct") is True, psych_columns.get("trial"))
    run.check("B: QPSY bounds the reaction time in milliseconds",
              (psych_columns["rt"]["type"], psych_columns["rt"]["min"],
               psych_columns["rt"]["max"]) == ("integer", 0, 5000), psych_columns.get("rt"))
    # KANO：正反两问共用**由模型固定**的五点量表，恰好张成分类表的 5×5 定义域；
    # 分类由平台按这两列算，信封里同样没有可以放「我属于 A 类」的地方。
    kano_columns = {column["code"]: column for column in run.side_tables.get("QKANO", {}).get("columns", [])}
    run.check("B: QKANO binding declares the feature column and both halves and nothing else",
              list(kano_columns) == ["feature", "functional", "dysfunctional"], list(kano_columns))
    run.check("B: both QKANO halves share the scale the model fixes",
              all([option["code"] for option in kano_columns[half]["options"]]
                  == ["like", "must", "neutral", "live", "dislike"]
                  for half in ("functional", "dysfunctional")), kano_columns)
    run.check("B: QKANO pins the feature column to the declared features and forbids repeats",
              [option["code"] for option in kano_columns.get("feature", {}).get("options", [])] == ["F1", "F2"]
              and kano_columns["feature"].get("distinct") is True, kano_columns.get("feature"))
    run.report["complete"] = {"{}[{}#{}]".format(*column): value for column, value in row.items()}


def scenario_blank(run: Run) -> None:
    print("scenario C: only mandatory questions answered — how the engine stores 'not answered'", file=sys.stderr)
    pages = blank_pages()
    steps = [{"answers": run.post(answers), "move": "movenext"} for answers in pages]
    steps[-1]["move"] = "movesubmit"
    result = run.respond(steps)
    run.check("C: completed with only the mandatory answers", result["completed"] is True, result["pages"][-1])
    row = run.last_row()
    run.report["blank"] = {"{}[{}#{}]".format(*column): ("NULL" if value is None else repr(value))
                           for column, value in row.items() if not column[0].startswith("_")}
    for code in ("QSCAN", "QSEC", "QGRPM"):
        cells = [value for (qcode, _, _), value in row.items() if qcode == code]
        run.check("C: unanswered {} stored as ''".format(code), cells and all(v == "" for v in cells), cells)
    blanks = [value for (qcode, aid, _), value in row.items() if qcode == "QBLANK"]
    run.check("C: an untouched inline-blank question stores '' everywhere",
              blanks and all(v == "" for v in blanks), blanks)


def scenario_tampers(run: Run) -> None:
    print("scenario D: tampered submissions — the respondent stays on the page", file=sys.stderr)
    pages = valid_pages()
    for tamper in TAMPERS:
        page_index = tamper["page"]
        steps = [{"answers": run.post(answers), "move": "movenext"} for answers in pages[:page_index]]
        answers = dict(pages[page_index])
        answers.update(tamper["answers"])
        posted = run.post(answers)
        posted.update(shadow_fields(run, tamper, posted))
        move = "movesubmit" if page_index == len(PAGES) - 1 else "movenext"
        steps.append({"answers": posted, "move": move})
        before = run.max_id()
        result = run.respond(steps)
        label = "D {}".format(tamper["label"])
        after = result["pages"][-1]
        marker = MARKERS[page_index]
        held = not result["completed"] and run.state(after, marker) == "visible"
        run.check("{}: rejected, respondent kept on page {}".format(label, page_index + 1), held, after)
        run.check(label + ": no submitted response from this session", run.submitted_since(before) == 0)


def scenario_boilerplate(run: Run) -> None:
    """说明文字题的那一列：表单里有一个同名 hidden input，实测引擎最终存了什么。"""
    print("scenario E: what the engine does with a value posted into the X column", file=sys.stderr)
    pages = valid_pages()
    answers = dict(pages[0])
    answers.update(BOILERPLATE_TAMPER["answers"])
    steps = [{"answers": run.post(answers), "move": "movenext"},
             {"answers": run.post(pages[1]), "move": "movesubmit"}]
    result = run.respond(steps)
    run.check("E: the survey still completes", result["completed"] is True, result["pages"][-1])
    stored = run.last_row()[("QSEC", "", 0)]
    run.report["boilerplateColumnAfterTamper"] = stored
    print("  [info] the X column after posting a value into it -> {!r}".format(stored), file=sys.stderr)


def shadow_fields(run: Run, tamper: Dict[str, Any], posted: Dict[str, str]) -> Dict[str, str]:
    """浏览器端算出的相关性副本；服务端一律重算，这里证明它确实重算了。"""
    if tamper.get("shadow") != "all-relevant":
        return {}
    shadow = {"relevance" + qid: "1" for qid in run.qids.values()}
    shadow.update({"relevance" + name: "1" for name in posted})
    return shadow


def scenario_response_read(run: Run, response_id: str, instance: str) -> None:
    print("scenario F: gateway response read returns every stored column", file=sys.stderr)
    names = [name for _, name in run.stored_columns()]
    engine = EngineConfig(instance, RPC_URL, ADMIN_USER, ADMIN_PASSWORD)
    service = ResponseReadService(engines={instance: engine}, secret=SECRET,
                                  transport_factory=lambda cfg: DockerCurlTransport(run.container))
    body = json.dumps({"engineInstanceId": instance, "surveyId": run.survey_id,
                       "responseIds": [int(response_id)], "fields": names}).encode("utf-8")
    stamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-Pubgw-Timestamp": stamp,
               "X-Pubgw-Signature": sign(SECRET, stamp, body)}
    response = service.read(headers, body)
    payload = json.loads(response.body.decode("utf-8"))
    run.check("F: HTTP 200 for every physical column", response.status == 200, payload)
    values = (payload.get("responses") or [{}])[0].get("values", {})
    by_name = {name: column for column, name in run.fields.items()}
    for name in names:
        key = "{}[{}#{}]".format(*by_name[name])
        stored = run.report["complete"].get(key)
        got = values.get(name)
        ok = (stored is None and got in (None, "")) or (stored is not None and got is not None and str(got) == stored)
        run.check("F: {} read back as stored".format(key), ok, {"db": stored, "read": got})


# ------------------------------------------------------------------ 主流程


def install_themes(container: str) -> Dict[str, Any]:
    report = docker_php(container, THEME_INSTALLER)
    if report["errors"]:
        raise RuntimeError("question theme install failed: {}".format(report["errors"]))
    return report


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

    installed = install_themes(args.container)
    print("installed {} question themes".format(len(installed["installed"])), file=sys.stderr)

    client = RemoteControlClient(DockerCurlTransport(args.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    survey_id = None
    try:
        published = publish(client, args.container)
        survey_id = published["surveyId"]
        print("published survey {} ({})".format(survey_id, published["binding"]["fingerprint"]), file=sys.stderr)
        run = Run(args.container, Database(args.db, args.db_container), args.db, survey_id, published["binding"])
        run.check("publish: every compiled column bound",
                  all(question["fields"] for question in published["binding"]["questions"]))
        run.check("publish: every side-table question declares a side table",
                  sorted(run.side_tables) == ["QHEAT", "QKANO", "QLOOP", "QMARK", "QPK", "QPSY",
                                              "QSHELF", "QTABLE"],
                  sorted(run.side_tables))

        response_id = scenario_render(run)
        scenario_values(run, response_id)
        scenario_response_read(run, response_id, args.container)
        scenario_blank(run)
        scenario_tampers(run)
        scenario_boilerplate(run)

        print(json.dumps({"database": args.db, "surveyId": survey_id, "failures": run.failures,
                          "blank": run.report.get("blank"),
                          "boilerplateColumnAfterTamper": run.report.get("boilerplateColumnAfterTamper")},
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
