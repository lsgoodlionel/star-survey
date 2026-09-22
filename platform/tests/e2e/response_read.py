#!/usr/bin/env python3

"""WP-06 切片 06.1 网关读端点端到端验证（在宿主机运行，见 run-response-read.sh）。

验证 ADR 0013 里唯一依赖引擎行为的假设：``export_responses``（JSON、代码表头、短答案、按区间）
按请求列的顺序输出每条记录，网关据此按位置把值还原到引擎列名上。

步骤：用网关的 Publisher 发布样例问卷（含"其他"、多选带评论、双尺度等多列题）；
直接往答卷表写三份答卷，每列一个互不相同的值；再经 ``ResponseReadService``（签名请求，
RemoteControl 走 ``docker exec curl``）读取第 1、3 号与一个不存在的答卷号。
断言每一列的值都落在正确的列名上、不存在的答卷报为 missing、第 2 号不被返回。
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pubgw.auth import sign  # noqa: E402
from pubgw.engines import EngineConfig  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.responses import ResponseReadService  # noqa: E402
from pubgw.rpc import RemoteControlClient, RpcError  # noqa: E402
from publish_gateway import (  # noqa: E402
    ADMIN_PASSWORD,
    ADMIN_USER,
    DEFINITION,
    Database,
    DockerCurlTransport,
    _response_table,
    info,
)

INSTANCE = "survey-test-web"
SECRET = b"e2e-response-read-secret-0123456789abcdef"
MISSING_ID = 999
#: 说明文字题（X）也占一列，但不收集作答，列类型可能很窄：不写它，期望读回空值。
DISPLAY_ONLY_TYPES = {"X"}


def cell(response_id: int, index: int) -> str:
    """每份答卷每列一个不同的值，且不超过选择题列的 5 个字符。"""
    return "{}{:02d}".format(response_id, index)


def insert_response(db: Database, driver: str, survey_id: int, response_id: int, columns: List[str]) -> None:
    quote = (lambda name: '"{}"'.format(name)) if driver == "pgsql" else (lambda name: "`{}`".format(name))
    names = ["id", "submitdate", "lastpage", "startlanguage", "seed", "startdate", "datestamp"] + columns
    values = [str(response_id), "'2026-09-22 10:00:00'", "2", "'en'", "'1'",
              "'2026-09-22 09:59:00'", "'2026-09-22 10:00:00'"]
    values += ["'{}'".format(cell(response_id, index)) for index in range(len(columns))]
    db.rows("INSERT INTO {} ({}) VALUES ({})".format(
        _response_table(survey_id), ", ".join(quote(n) for n in names), ", ".join(values)))


def read(service: ResponseReadService, survey_id: int, ids: List[int], fields: List[str]) -> Dict:
    body = json.dumps({"engineInstanceId": INSTANCE, "surveyId": survey_id,
                       "responseIds": ids, "fields": fields}).encode("utf-8")
    stamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-Pubgw-Timestamp": stamp,
               "X-Pubgw-Signature": sign(SECRET, stamp, body)}
    response = service.read(headers, body)
    return {"status": response.status, "body": json.loads(response.body.decode("utf-8"))}


def main() -> int:
    parser = argparse.ArgumentParser(description="06.1 网关读端点端到端验证")
    parser.add_argument("--container", default="survey-test-web")
    parser.add_argument("--db", default="mysql", choices=("mysql", "pgsql"))
    args = parser.parse_args()

    definition = SurveyDefinition.from_json(DEFINITION.read_text(encoding="utf-8"))
    db = Database(args.db)
    client = RemoteControlClient(DockerCurlTransport(args.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    survey_id = None
    try:
        outcome = Publisher(client, engine_instance=INSTANCE).publish(definition)
        if not outcome.ok:
            info("发布失败：{}".format(outcome.failures))
            return 1
        survey_id = outcome.survey_id
        bound = [(field.fieldname, question.type) for question in outcome.binding.questions
                 for field in question.fields]
        fields = [name for name, _ in bound]
        written = [name for name, qtype in bound if qtype not in DISPLAY_ONLY_TYPES]
        for response_id in (1, 2, 3):
            insert_response(db, args.db, survey_id, response_id, written)

        engine = EngineConfig(INSTANCE, "http://localhost/index.php/admin/remotecontrol", ADMIN_USER,
                              ADMIN_PASSWORD)
        service = ResponseReadService(engines={INSTANCE: engine}, secret=SECRET,
                                      transport_factory=lambda cfg: DockerCurlTransport(args.container))
        reply = read(service, survey_id, [3, 1, MISSING_ID], fields)

        body = reply["body"]
        got = {entry["id"]: entry["values"] for entry in body.get("responses", [])}
        expected = {
            rid: {name: (cell(rid, written.index(name)) if name in written else None) for name in fields}
            for rid in (1, 3)
        }
        checks = {
            "HTTP 200": reply["status"] == 200,
            "只返回请求的答卷": sorted(got) == [1, 3],
            "不存在的答卷报为 missing": body.get("missing") == [MISSING_ID],
            "每列的值落在正确的列名上": got == expected,
        }
        passed = all(checks.values())
        info(("PASS " if passed else "FAIL ") + "export_responses 按位置还原列名")
        for label, ok in checks.items():
            if not ok:
                info("  失败断言：" + label)
        print(json.dumps({"database": args.db, "passed": passed, "checks": checks, "surveyId": survey_id,
                          "columns": len(fields), "reply": body, "expected": expected},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if passed else 1
    finally:
        if survey_id is not None:
            try:
                client.delete_survey(survey_id)
            except RpcError as error:
                info("清理问卷 {} 失败：{}".format(survey_id, error))
        client.logout()


if __name__ == "__main__":
    raise SystemExit(main())
