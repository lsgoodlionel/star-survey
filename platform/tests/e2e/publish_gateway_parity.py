#!/usr/bin/env python3

"""WP-03.4 双执行比对：平台按 DSL 自己算一遍，和真引擎算的逐条比，出具证据表。

在宿主机运行，见 platform/deploy/test/run-publish-gateway-parity.sh。

1. **发布**：platform/tests/fixtures/surveys/publish-gateway-scoring.json（带计分表的
   v2 定义）走完整七阶段，计分被展开成计算值题与结果说明题。
2. **表达式级比对**：定义里每一处表达式 × 每一份答案向量各是一条用例。平台侧用
   pubgw.logic.evaluate 在语法树上求值；引擎侧由 logic_parity.php 把同一份答案注入
   $_SESSION，再用真 ExpressionManager 求值网关编出来的 ExpressionScript。
   每条用例都带一个照契约人工写下的期望值，分歧时用它判定是哪一侧错了。
3. **场景级锚定**：再跑两次真实 HTTP 作答，把引擎**真正存进答卷表**的分数与分段，
   同平台算出来的值比——证明第 2 步注入的那套会话不是自说自话。

任何一条不一致（或反向对照没被抓住）都让整个脚本以非零退出。
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, DockerCurlTransport  # noqa: E402
from publish_gateway_logic import NamedDatabase, docker_php  # noqa: E402
from pubgw.logic.parity import (  # noqa: E402
    BLAME_NONE,
    AnswerVector,
    ParityCase,
    Verdict,
    adjudicate,
    build_cases,
    summarize,
)
from pubgw.logic.scoring import expand_scoring  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402

FIXTURES = REPO_ROOT / "platform/tests/fixtures/surveys"
DEFINITION = FIXTURES / "publish-gateway-scoring.json"
VECTORS = FIXTURES / "publish-gateway-scoring.vectors.json"
PARITY = "platform/tests/e2e/logic_parity.php"
RESPONDER = "platform/tests/e2e/logic_respond.php"

#: 场景级锚定：真实作答 → 引擎存进答卷表的分数必须等于平台算出来的分数。
ANCHORS = (
    {
        "name": "dog/mid",
        "pages": [
            {"QAGE": "30", "QPET": "A2", "QNAME": "Ann"},
            {"QFREQ": "F1", "QHOURS": "1"},
        ],
        "answers": {"QAGE": "30", "QPET": "A2", "QNAME": "Ann", "QFREQ": "F1", "QHOURS": "1"},
    },
    {
        "name": "cat/low",
        "pages": [
            {"QAGE": "44", "QPET": "A1", "QNAME": "Bo"},
            {"QFREQ": "F3", "QHOURS": "1"},
        ],
        "answers": {"QAGE": "44", "QPET": "A1", "QNAME": "Bo", "QFREQ": "F3", "QHOURS": "1"},
    },
)


class Report:
    def __init__(self) -> None:
        self.failures: List[str] = []

    def check(self, label: str, ok: bool, detail: Any = "") -> None:
        print("  [{}] {}{}".format("ok" if ok else "FAIL", label, "" if ok else ": {}".format(detail)),
              file=sys.stderr)
        if not ok:
            self.failures.append(label)


# ------------------------------------------------------------------ 发布


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


def load_vectors() -> List[AnswerVector]:
    payload = json.loads(VECTORS.read_text(encoding="utf-8"))
    return [
        AnswerVector(
            name=entry["name"],
            answers=entry.get("answers", {}),
            hidden=tuple(entry.get("hidden", ())),
            expected=entry.get("expected", {}),
        )
        for entry in payload["vectors"]
    ]


def field_map(published: Dict[str, Any]) -> Dict[str, str]:
    """引擎变量名（qcode 形式）→ 答卷列名，多选按子题展开。"""
    fields: Dict[str, str] = {}
    for question in published["binding"]["questions"]:
        for field in question["fields"]:
            aid = str(field.get("aid") or "")
            scale = field.get("scale")
            name = question["code"]
            if aid:
                name += "_" + aid
            if scale not in (None, 0, "0"):
                name += "_" + str(scale)
            fields[name] = field["fieldname"]
    return fields


# ------------------------------------------------------------------ 表达式级比对


def run_engine(container: str, survey_id: int, cases: Sequence[ParityCase]) -> Dict[str, Any]:
    plan = json.dumps({"cases": [case.to_plan() for case in cases]}).encode("utf-8")
    return docker_php(container, PARITY, str(survey_id), stdin=plan)


def compare(cases: Sequence[ParityCase], engine: Dict[str, Any]) -> List[Verdict]:
    results = {str(item["id"]): item for item in engine["results"]}
    verdicts = []
    for case in cases:
        result = results.get(case.id)
        if result is None:
            verdicts.append(adjudicate(case, "", ["engine returned no result for this case"]))
            continue
        verdicts.append(adjudicate(case, str(result["value"]), [str(e) for e in result["errors"]]))
    return verdicts


def mutants(cases: Sequence[ParityCase]) -> List[ParityCase]:
    """故意把两边各弄错一次：比对必须抓住，而且要指对是哪一侧。

    没有这一步，「48 条全对」只证明两边跑完了，不证明比对还能报错。
    """
    seed = next(case for case in cases if case.path.endswith("questions[1].calculation")
                and case.vector == "dog")
    return [
        # 编译产物被改坏（<= 变成 >=），平台与期望不动 → 该判编译器错。
        seed.replace(vector="mutant-compiler", compiled=seed.compiled.replace("<=", ">=")),
        # 编译产物不动，平台谎报一个值 → 该判解释器错。
        seed.replace(vector="mutant-platform", platform="NOT-A-BAND"),
    ]


def check_mutants(report: Report, container: str, survey_id: int, cases: Sequence[ParityCase]) -> None:
    planted = mutants(cases)
    verdicts = compare(planted, run_engine(container, survey_id, planted))
    wanted = {"mutant-compiler": "compiler", "mutant-platform": "platform"}
    for verdict in verdicts:
        expected_blame = wanted[verdict.case.vector]
        report.check(
            "反向对照：{} 被抓住并判给 {}".format(verdict.case.vector, expected_blame),
            not verdict.agree and verdict.blame == expected_blame,
            "blame={} 平台={!r} 引擎={!r}".format(verdict.blame, verdict.case.platform, verdict.engine),
        )


HEADERS = ("向量", "位置", "DSL 表达式", "平台", "引擎", "期望", "归责")


def print_table(verdicts: Sequence[Verdict]) -> None:
    rows = [HEADERS] + [verdict.row() for verdict in verdicts]
    widths = [max(_width(row[index]) for row in rows) for index in range(len(HEADERS))]
    print("\n双执行比对表（平台解释 DSL ⇄ 真引擎跑编译产物）", file=sys.stderr)
    for position, row in enumerate(rows):
        print("  " + " | ".join(_pad(cell, widths[index]) for index, cell in enumerate(row)), file=sys.stderr)
        if position == 0:
            print("  " + "-+-".join("-" * width for width in widths), file=sys.stderr)


def _width(text: str) -> int:
    return sum(2 if ord(char) > 0x2E80 else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


# ------------------------------------------------------------------ 场景级锚定


def respond(container: str, survey_id: int, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    plan = json.dumps({"steps": steps}).encode("utf-8")
    return docker_php(container, RESPONDER, str(survey_id), stdin=plan)


def stored_row(db: NamedDatabase, survey_id: int, fields: Dict[str, str], wanted: Sequence[str]) -> Dict[str, str]:
    quote = (lambda name: '"{}"'.format(name)) if db._driver == "pgsql" else (lambda name: "`{}`".format(name))
    cast = "TEXT" if db._driver == "pgsql" else "CHAR(200)"
    select = ", ".join(
        "COALESCE(CAST({} AS {}), '<NULL>')".format(quote(fields[code]), cast) for code in wanted
    )
    rows = db.rows(
        "SELECT {} FROM lime_responses_{} ORDER BY id DESC LIMIT 1".format(select, survey_id)
    )
    return {code: (None if value == "<NULL>" else value) for code, value in zip(wanted, rows[0])}


def anchor(report: Report, container: str, db: NamedDatabase, survey_id: int,
           fields: Dict[str, str], definition: SurveyDefinition) -> None:
    print("\n场景级锚定：真实 HTTP 作答，引擎存进答卷表的分数 ⇄ 平台算出来的分数", file=sys.stderr)
    for case in ANCHORS:
        steps = [
            {"answers": {fields[code]: value for code, value in page.items()}, "move": "movenext"}
            for page in case["pages"]
        ]
        steps.append({"answers": {}, "move": "movesubmit"})
        result = respond(container, survey_id, steps)
        report.check("{}: 作答完成".format(case["name"]), result["completed"] is True, result)
        stored = stored_row(db, survey_id, fields, ("STOTAL", "STOTALB"))
        vector = AnswerVector(name=case["name"], answers=case["answers"])
        platform = {
            path.rsplit(".", 1)[0]: value
            for path, value in _platform_scores(definition, vector).items()
        }
        report.check(
            "{}: 引擎存的总分 {} == 平台算的 {}".format(case["name"], stored["STOTAL"], platform["STOTAL"]),
            _same_number(stored["STOTAL"], platform["STOTAL"]),
            stored,
        )
        report.check(
            "{}: 引擎存的分段 {} == 平台算的 {}".format(case["name"], stored["STOTALB"], platform["STOTALB"]),
            stored["STOTALB"] == platform["STOTALB"],
            stored,
        )


def _platform_scores(definition: SurveyDefinition, vector: AnswerVector) -> Dict[str, str]:
    """平台侧按同一份答案算出总分，再把总分喂回去算分段（引擎也是这个顺序）。"""
    total = _calculation_of(definition, vector, "STOTAL")
    filled = AnswerVector(vector.name, dict(vector.answers, STOTAL=total), vector.hidden)
    return {
        "STOTAL.calculation": total,
        "STOTALB.calculation": _calculation_of(definition, filled, "STOTALB"),
    }


def _calculation_of(definition: SurveyDefinition, vector: AnswerVector, code: str) -> str:
    for case in build_cases(definition, [vector]):
        if case.path.endswith(".calculation") and case.source.startswith(("sum(", "if(")):
            owner = _owner_code(definition, case.path)
            if owner == code:
                return case.platform
    raise KeyError(code)


def _owner_code(definition: SurveyDefinition, path: str) -> str:
    group_index = int(path.split("[", 1)[1].split("]", 1)[0])
    question_index = int(path.split("questions[", 1)[1].split("]", 1)[0])
    return definition.groups[group_index].questions[question_index].code


def _same_number(stored: Any, platform: str) -> bool:
    if stored is None:
        return platform == ""
    try:
        return float(stored) == float(platform)
    except ValueError:
        return str(stored) == platform


# ------------------------------------------------------------------ 主流程


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="survey-test-web")
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", default=None)
    args = parser.parse_args()
    db_container = args.db_container or ("survey-test-pg" if args.db == "pgsql" else "survey-test-db")

    report = Report()
    published = publish(args.container)
    survey_id = published["surveyId"]
    print("published survey {} ({})".format(survey_id, published["binding"]["fingerprint"]), file=sys.stderr)

    definition = expand_scoring(SurveyDefinition.from_json(DEFINITION.read_text(encoding="utf-8")))
    vectors = load_vectors()
    cases = build_cases(definition, vectors)
    report.check("每条用例都带人工写下的期望值（否则分歧无从裁决）",
                 all(case.expected is not None for case in cases),
                 [case.id for case in cases if case.expected is None])

    engine = run_engine(args.container, survey_id, cases)
    report.check("引擎认得比对里用到的每一个变量", not engine["unknownVariables"], engine["unknownVariables"])
    report.check("反向对照：未知变量、语法错误、算错的值都被抓住",
                 all(engine["canariesCaught"].values()), engine["canariesCaught"])

    verdicts = compare(cases, engine)
    print_table(verdicts)
    counts = summarize(verdicts)
    print("\n比对了 {cases} 条，其中 {adjudicated} 条带期望值；一致 {agreed} 条".format(**counts), file=sys.stderr)
    for blame in ("platform", "compiler", "both", "unknown"):
        if counts[blame]:
            print("  归责 {}：{} 条".format(blame, counts[blame]), file=sys.stderr)
            for verdict in verdicts:
                if verdict.blame == blame:
                    print("    {} → 平台 {!r} / 引擎 {!r} / 期望 {!r} {}".format(
                        verdict.case.id, verdict.case.platform, verdict.engine,
                        verdict.case.expected, verdict.errors), file=sys.stderr)
    report.check("表达式级比对：{} 条全部一致且合期望".format(counts["cases"]),
                 counts[BLAME_NONE] == counts["cases"], counts)
    check_mutants(report, args.container, survey_id, cases)

    db = NamedDatabase(args.db, db_container)
    anchor(report, args.container, db, survey_id, field_map(published), definition)

    print(json.dumps({"surveyId": survey_id, "parity": counts, "failures": report.failures}))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
