#!/usr/bin/env python3

"""P0-00.8 发布网关端到端验证（在宿主机运行，见 run-publish-gateway.sh）。

四个场景：

1. **正常发布**：定义 → LSS → 导入 → 补发 → 激活 → 回读校验 → 绑定记录，
   并在库里核对答卷表确实建好、列数与 fieldmap 一致。
2. **定义被拒**：缺答案选项的问卷连引擎都不碰；同一份结构绕过网关直接用
   RemoteControl 发，引擎照样激活成功——这正是网关存在的理由。
3. **强制改名 → 回滚**：编译产物被人为改坏，引擎导入时自动改名，
   回读校验判负、`delete_survey` 回滚；断言库里不留任何痕迹，
   且场景一那份已发布问卷仍在服役。
4. **漂移检测**：激活后用 `set_question_properties` 改题目代码，
   引擎接受、答卷表列不变，漂移检查必须把它揪出来。

引擎容器没有 Python，测试栈也没有对宿主暴露端口，所以 RemoteControl 的
传输层走 `docker exec <容器> curl`，SQL 走 `docker exec <数据库容器>`。
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))

from pubgw.binding import BindingRecord  # noqa: E402
from pubgw.compiler import LssCompiler  # noqa: E402
from pubgw.drift import check_drift  # noqa: E402
from pubgw.fieldmap import parse_fieldmap  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient, RpcError  # noqa: E402
from pubgw.validate import validate_definition  # noqa: E402

DEFINITION = REPO_ROOT / "platform/tests/fixtures/surveys/publish-gateway.json"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "password"
RPC_TIMEOUT_SECONDS = 180
TABLE_PREFIX = "lime_"


# ------------------------------------------------------------------ 传输


class DockerCurlTransport:
    """把 JSON-RPC 请求塞进容器里的 curl。测试栈没有对宿主暴露端口。"""

    def __init__(self, container: str, url: str = "http://localhost/index.php/admin/remotecontrol"):
        self._container = container
        self._url = url

    def __call__(self, payload: bytes) -> bytes:
        command = [
            "docker", "exec", "-i", self._container,
            "curl", "-s", "--max-time", str(RPC_TIMEOUT_SECONDS),
            "-H", "Content-Type: application/json",
            "--data-binary", "@-", self._url,
        ]
        completed = subprocess.run(command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            raise RuntimeError("docker exec curl failed: " + completed.stderr.decode("utf-8", "replace"))
        return completed.stdout


class Database:
    """只读 SQL 探针：网关自己不碰数据库，验证脚本需要看库里的事实。"""

    def __init__(self, driver: str):
        self._driver = driver

    def rows(self, sql: str) -> List[List[str]]:
        if self._driver == "pgsql":
            command = ["docker", "exec", "survey-test-pg", "psql", "-U", "postgres",
                       "-d", "limesurvey", "-tA", "-F", "\t", "-c", sql]
        else:
            command = ["docker", "exec", "survey-test-db", "mariadb", "-uroot", "-proot",
                       "-N", "-B", "limesurvey", "-e", sql]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if completed.returncode != 0:
            raise RuntimeError("sql failed: " + completed.stderr.decode("utf-8", "replace"))
        text = completed.stdout.decode("utf-8").strip()
        return [line.split("\t") for line in text.splitlines() if line]

    def value(self, sql: str) -> str:
        rows = self.rows(sql)
        return rows[0][0] if rows else ""

    def has_table(self, name: str) -> bool:
        return self.value(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{}'".format(name)
        ) not in ("0", "")

    def columns(self, table: str) -> List[str]:
        return [
            row[0]
            for row in self.rows(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = '{}' ORDER BY column_name".format(table)
            )
        ]

    def count(self, table: str, where: str = "1=1") -> int:
        return int(self.value("SELECT COUNT(*) FROM {}{} WHERE {}".format(TABLE_PREFIX, table, where)) or 0)

    def survey_active(self, survey_id: int) -> str:
        return self.value(
            "SELECT active FROM {}surveys WHERE sid = {}".format(TABLE_PREFIX, survey_id)
        )


# ------------------------------------------------------------------ 工具


class TamperedCompiler(LssCompiler):
    """编译正常但产出被改坏，模拟「编译器有 bug」或「中途被人动了手脚」。"""

    def __init__(self, replacements: Dict[str, str]):
        self._replacements = replacements

    def compile(self, definition: SurveyDefinition):
        compiled = LssCompiler().compile(definition)
        lss = compiled.lss
        for old, new in self._replacements.items():
            lss = lss.replace("[CDATA[{}]]".format(old), "[CDATA[{}]]".format(new))
        return type(compiled)(
            lss=lss,
            compiler_version=compiled.compiler_version,
            signature=compiled.signature,
            fingerprint=compiled.fingerprint,
            definition_uuid=compiled.definition_uuid,
        )


class StrippedAnswersCompiler(LssCompiler):
    """把 answers/answer_l10ns 两个小节整段删掉，产出一道没有选项的单选题。"""

    def compile(self, definition: SurveyDefinition):
        compiled = LssCompiler().compile(definition)
        lss = compiled.lss
        for section in ("answers", "answer_l10ns"):
            lss = _drop_section(lss, section)
        return type(compiled)(
            lss=lss,
            compiler_version=compiled.compiler_version,
            signature=compiled.signature,
            fingerprint=compiled.fingerprint,
            definition_uuid=compiled.definition_uuid,
        )


def _drop_section(document: str, name: str) -> str:
    opening = "\n <{}>".format(name)
    closing = "\n </{}>".format(name)
    start = document.find(opening)
    if start < 0:
        return document
    end = document.find(closing, start)
    return document[:start] + document[end + len(closing):]


def _response_table(survey_id: int) -> str:
    """答卷表名（application/models/Survey.php:811）。"""
    return "{}responses_{}".format(TABLE_PREFIX, survey_id)


def info(message: str) -> None:
    sys.stderr.write("[e2e] {}\n".format(message))


def result(scenario: str, checks: Dict[str, bool], evidence: Dict[str, Any]) -> Dict[str, Any]:
    passed = all(checks.values())
    info(("PASS " if passed else "FAIL ") + scenario)
    for label, ok in checks.items():
        if not ok:
            info("  失败断言：" + label)
    return {"scenario": scenario, "passed": passed, "checks": checks, "evidence": evidence}


# ------------------------------------------------------------------ 场景


def scenario_happy_path(client, db, definition) -> Tuple[Dict[str, Any], BindingRecord]:
    publisher = Publisher(client, engine_instance="survey-test-web")
    outcome = publisher.publish(definition)
    record = outcome.binding
    survey_id = outcome.survey_id

    response_columns = db.columns(_response_table(survey_id)) if survey_id else []
    bound_fields = [field.fieldname for item in record.questions for field in item.fields] if record else []

    checks = {
        "发布成功": outcome.ok,
        "走完全部阶段": [step.stage for step in outcome.steps][-1] == "bind",
        "问卷已激活": db.survey_active(survey_id) == "Y",
        "答卷表已建立": bool(response_columns),
        "绑定记录覆盖全部题目": record is not None and len(record.questions) == 5,
        "说明文字题也占一列（引擎行为，不是平台选择）": record is not None
        and len(next(item for item in record.questions if item.code == "QNOTE").fields) == 1,
        "绑定的字段名全部真实存在于答卷表": all(
            name in response_columns for name in bound_fields
        ),
        "指纹与编译期一致": outcome.verification is not None
        and outcome.verification.fingerprint == outcome.verification.expected_fingerprint,
        "没有回滚": not outcome.rolled_back,
    }
    evidence = {
        "surveyId": survey_id,
        "stages": [step.stage for step in outcome.steps],
        "fingerprint": record.fingerprint if record else None,
        "binding": record.to_dict() if record else None,
        "responseColumns": response_columns,
        "failures": outcome.failures,
    }
    return result("正常发布", checks, evidence), record


def scenario_rejected_definition(client, db, definition) -> Dict[str, Any]:
    """缺答案选项：网关拒绝，而绕过网关的同一份结构会被引擎顺利激活。"""
    payload = json.loads(DEFINITION.read_text(encoding="utf-8"))
    payload["groups"][0]["questions"][0]["answers"] = []
    broken = SurveyDefinition.from_dict(payload)

    surveys_before = db.count("surveys")
    outcome = Publisher(client, engine_instance="survey-test-web").publish(broken)
    surveys_after = db.count("surveys")

    report = validate_definition(broken)

    # 同一个缺陷绕过网关直接发给引擎：证明 activate_survey 真的不查选项。
    stripped = StrippedAnswersCompiler().compile(definition)
    bypass_id = client.import_survey(stripped.lss, "P0 bypass")
    bypass_response = client.call("activate_survey", [client.session_key, bypass_id])
    bypass_columns = db.columns(_response_table(bypass_id))
    answer_rows = db.count(
        "answers",
        "qid IN (SELECT qid FROM {}questions WHERE sid = {})".format(TABLE_PREFIX, bypass_id),
    )
    client.delete_survey(bypass_id)

    checks = {
        "网关在 validate 阶段就拒绝": outcome.failed_stage == "validate",
        "拒绝理由是缺答案选项": "E_MISSING_ANSWERS" in " ".join(outcome.failures),
        "引擎完全没有被碰过": surveys_after == surveys_before,
        "没有产生问卷 id": outcome.survey_id is None,
        "校验报告只报这一条结构问题": [i.code for i in report.issues] == ["E_MISSING_ANSWERS"],
        "绕过网关时引擎照样激活成功（引擎缺口）": _accepted(bypass_response),
        "被激活的问卷确实一个选项都没有": answer_rows == 0 and bool(bypass_columns),
    }
    evidence = {
        "failures": outcome.failures,
        "bypassSurveyId": bypass_id,
        "bypassActivateResponse": bypass_response,
        "bypassAnswerRows": answer_rows,
    }
    return result("定义被拒 ＋ 引擎缺口对照", checks, evidence)


def scenario_forced_rename(
    client, db, definition, published: BindingRecord
) -> Tuple[Dict[str, Any], Any]:
    """编译产物里塞一个非法代码，引擎导入时自动改名 → 回读判负 → 回滚。"""
    # 代码必须挑得「修复后与原值不同」：引擎先 preg_replace 掉非字母数字，
    # 所以 "Q-SINGLE!" 会被修回 "QSINGLE"，反而验证不出问题。数字开头的代码
    # 会被加上前缀 q（import_helper.php:2649-2651），结果必然不同。
    compiler = TamperedCompiler({"QSINGLE": "9QSINGLE"})
    surveys_before = db.count("surveys")
    outcome = Publisher(client, engine_instance="survey-test-web", compiler=compiler).publish(definition)
    survey_id = outcome.survey_id

    leftovers = {
        "surveys": db.count("surveys", "sid = {}".format(survey_id)),
        "groups": db.count("groups", "sid = {}".format(survey_id)),
        "questions": db.count("questions", "sid = {}".format(survey_id)),
        "permissions": db.count("permissions", "entity = 'survey' AND entity_id = {}".format(survey_id)),
    }
    checks = {
        "导入成功但校验判负": survey_id is not None and not outcome.ok,
        "失败发生在激活之前": outcome.failed_stage == "apply",
        "识别出引擎的自动改名": "E_CODE_RENAMED" in " ".join(outcome.failures),
        "已回滚": outcome.rolled_back,
        "没有留下孤儿问卷": outcome.orphan_survey_id is None,
        "库里不留任何结构行": all(count == 0 for count in leftovers.values()),
        "没有留下答卷表": not db.has_table(_response_table(survey_id)),
        "问卷总数回到发布前": db.count("surveys") == surveys_before,
        "上一版仍在服役": db.survey_active(published.survey_id) == "Y",
    }
    evidence = {
        "surveyId": survey_id,
        "failures": outcome.failures,
        "renamed": [
            {"from": old, "to": new}
            for old, new in (outcome.verification.renamed if outcome.verification else ())
        ],
        "leftoverRows": leftovers,
    }
    leaked = survey_id if (survey_id is not None and not outcome.rolled_back) else None
    return result("强制改名 ＋ 回滚", checks, evidence), leaked


def scenario_drift(client, db, record: BindingRecord) -> Dict[str, Any]:
    """激活后改题目代码：引擎接受、答卷列不变，只有指纹和绑定映射能发现。"""
    table = _response_table(record.survey_id)
    columns_before = db.columns(table)
    baseline = check_drift(record, parse_fieldmap(client.get_fieldmap(record.survey_id)))

    target = next(item for item in record.questions if item.code == "QSINGLE")
    qid = db.value(
        "SELECT qid FROM {}questions WHERE sid = {} AND parent_qid = 0 AND title = '{}'".format(
            TABLE_PREFIX, record.survey_id, target.code
        )
    )
    rename_response = client.call(
        "set_question_properties", [client.session_key, int(qid), {"title": "QDRIFTED"}]
    )

    after = check_drift(record, parse_fieldmap(client.get_fieldmap(record.survey_id)))
    columns_after = db.columns(table)

    checks = {
        "改名前没有漂移": not baseline.drifted,
        "引擎接受了激活后的改名": _accepted(rename_response),
        "漂移被检出": after.drifted,
        "指纹发生变化": after.current_fingerprint != after.recorded_fingerprint,
        "报出具体是哪道题改成了什么": after.renamed == ((target.uuid, "QSINGLE", "QDRIFTED"),),
        "答卷表的列一个都没变（这正是危险之处）": columns_after == columns_before,
    }
    evidence = {
        "surveyId": record.survey_id,
        "recordedFingerprint": after.recorded_fingerprint,
        "currentFingerprint": after.current_fingerprint,
        "renameResponse": rename_response,
        "issues": [issue.code for issue in after.issues],
        "responseColumns": columns_after,
    }
    return result("激活后漂移检测", checks, evidence)


def _accepted(response: Any) -> bool:
    from pubgw.rpc import is_accepted

    return is_accepted(response)


# ------------------------------------------------------------------ 主流程


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-00.8 发布网关端到端验证")
    parser.add_argument("--container", default="survey-test-web")
    parser.add_argument("--db", default="mysql", choices=("mysql", "pgsql"))
    args = parser.parse_args()

    definition = SurveyDefinition.from_json(DEFINITION.read_text(encoding="utf-8"))
    db = Database(args.db)
    client = RemoteControlClient(DockerCurlTransport(args.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)

    created: List[int] = []
    try:
        happy, record = scenario_happy_path(client, db, definition)
        if record is not None:
            created.append(record.survey_id)
        scenarios = [happy]
        scenarios.append(scenario_rejected_definition(client, db, definition))
        if record is None:
            info("正常发布没有产出绑定记录，后续场景无法进行")
            scenarios.append({"scenario": "强制改名 ＋ 回滚", "passed": False, "checks": {}, "evidence": {}})
            scenarios.append({"scenario": "激活后漂移检测", "passed": False, "checks": {}, "evidence": {}})
        else:
            rollback, leaked = scenario_forced_rename(client, db, definition, record)
            if leaked is not None:
                created.append(leaked)
            scenarios.append(rollback)
            scenarios.append(scenario_drift(client, db, record))
    finally:
        for survey_id in created:
            try:
                client.delete_survey(survey_id)
            except RpcError as error:
                info("清理问卷 {} 失败：{}".format(survey_id, error))
        client.logout()

    summary = {"database": args.db, "scenarios": scenarios}
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(scenario["passed"] for scenario in scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
