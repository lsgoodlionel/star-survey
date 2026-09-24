#!/usr/bin/env python3

"""WP-10 客观题自动评分端到端：真引擎作答，服务端判分，作答者改不了分。

在宿主机运行，见 platform/deploy/test/run-exam-grading.sh。

判分接在 WP-09.1 的答案键之上：答案只存在于 plugin_settings（从不渲染进作答页），
对错在服务端按「答案键 vs 答卷表里的答案」推出来。作答者提交不了"我答对了"——
答卷表里根本没有可以放对错的列（与 mjy-psych-trial 的列定义同一条原则）。

## 场景

G1 一份对错混合的卷子交上去，分数、对题数、满分都对得上。
G2 成绩明细里**没有正确答案**：成绩是要给人看、要导出的东西，正确答案跟着它跑
   一圈就等于绕过 WP-09.1 又下发了一次。
G3 成绩不在答卷表里，只在插件表里；答卷表里没有任何一列是分数。
G4 **篡改**：POST 里塞 score / correct / mjy_exam_score 之类的字段，分数不变。
G5 重复判分不会留下两份成绩。
G6 没答的题按 0 分计，满分仍按答案键算（不因为没答就缩水）。
"""

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "platform" / "tools" / "publish-gateway"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_gateway import ADMIN_PASSWORD, ADMIN_USER, Database, DockerCurlTransport  # noqa: E402
from pubgw.model import SurveyDefinition  # noqa: E402
from pubgw.publish import Publisher  # noqa: E402
from pubgw.rpc import RemoteControlClient  # noqa: E402
from exam_support import response_fields  # noqa: E402

RESPONDER = "platform/tests/e2e/access_respond.php"
PLUGIN = "MjyRuntimePolicy"
TEXT_SENTINEL = "MJYSENTINELGRADE5W2"
RIGHT, WRONG = "AO01", "AO02"


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
        return "/tmp/exam-grading-{}-{}.cookies".format(uuid.uuid4().hex[:8], self.jars)

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

    def fields(self, survey_id: int) -> Dict[str, str]:
        return response_fields(self.db, survey_id)

    def score_of(self, survey_id: int, response_id: str) -> Optional[Dict[str, str]]:
        rows = self.db.rows(
            "SELECT score, max_score, correct_count, question_count, detail "
            "FROM lime_mjyruntimepolicy_exam_score "
            "WHERE survey_id = {} AND response_id = {}".format(survey_id, response_id))
        if not rows:
            return None
        return dict(zip(("score", "max_score", "correct_count", "question_count", "detail"), rows[0]))

    def submitted_ids(self, survey_id: int) -> List[str]:
        return [row[0] for row in self.db.rows(
            "SELECT id FROM lime_responses_{} WHERE submitdate IS NOT NULL ORDER BY id".format(survey_id))]


# ------------------------------------------------------------------ 定义与发布


def definition(title: str) -> Dict[str, Any]:
    return {
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
            "uuid": str(uuid.uuid4()), "title": "试卷",
            "questions": [
                {
                    "uuid": str(uuid.uuid4()), "code": "QPICK", "type": "L", "text": "单选题",
                    "answers": [{"code": RIGHT, "text": "甲"}, {"code": WRONG, "text": "乙"}],
                },
                {
                    "uuid": str(uuid.uuid4()), "code": "QMULTI", "type": "M", "text": "多选题",
                    "subquestions": [
                        {"uuid": str(uuid.uuid4()), "code": "SQ001", "text": "一"},
                        {"uuid": str(uuid.uuid4()), "code": "SQ002", "text": "二"},
                        {"uuid": str(uuid.uuid4()), "code": "SQ003", "text": "三"},
                    ],
                },
                {"uuid": str(uuid.uuid4()), "code": "QCITY", "type": "S", "text": "填空题"},
            ],
        }],
        "exam": {
            "examVersion": 1,
            "answerKey": [
                {"question": "QPICK", "correct": [RIGHT], "points": 5},
                {"question": "QMULTI", "correct": ["SQ001", "SQ003"], "points": 4},
                {"question": "QCITY", "correct": [TEXT_SENTINEL], "points": 3, "ignoreCase": True},
            ],
        },
    }


def publish_ok(context: Context, payload: Dict[str, Any]) -> int:
    client = RemoteControlClient(DockerCurlTransport(context.container))
    client.login(ADMIN_USER, ADMIN_PASSWORD)
    try:
        result = Publisher(client, engine_instance=context.container).publish(
            SurveyDefinition.from_dict(payload)).to_dict()
    finally:
        client.logout()
    if not result["ok"]:
        raise RuntimeError("publish failed at {}: {}".format(result["failedStage"], result["failures"]))
    print("published {} as sid {}".format(payload["title"], result["surveyId"]), file=sys.stderr)
    return result["surveyId"]


def start_url(survey_id: int) -> str:
    return "/index.php/{}?newtest=Y&lang=en".format(survey_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--db", choices=("mysql", "pgsql"), default="mysql")
    parser.add_argument("--db-container", required=True)
    args = parser.parse_args()
    context = Context(args.container, Database(args.db, args.db_container))

    sid = publish_ok(context, definition("WP-10 客观题自动评分"))
    fields = context.fields(sid)

    # -------------------------------------------------------------- G1 判分
    print("G1: 一份对错混合的卷子", file=sys.stderr)
    # 单选答对（5）；多选只选了一个，整套不对（0）；填空大小写不同，按 ignoreCase 算对（3）。
    answers = {
        fields["QPICK"]: RIGHT,
        fields["QMULTI.SQ001"]: "Y",
        fields["QCITY"]: TEXT_SENTINEL.lower(),
    }
    pages = context.respond(context.new_jar(), [
        {"get": start_url(sid)},
        {"submit": answers, "move": "movesubmit"},
    ])
    context.check("G1: 交卷成功", pages[1]["kind"] == "completed", pages[1]["text"][:200])

    ids = context.submitted_ids(sid)
    context.check("G1: 答卷表里有一份已交卷的答卷", len(ids) == 1, ids)
    score = context.score_of(sid, ids[0])
    context.check("G1: 判出了成绩", score is not None)
    if score is None:
        print(json.dumps({"failures": context.failures}))
        return 1
    context.check("G1: 得分是 8（5 + 0 + 3）", float(score["score"]) == 8.0, score)
    context.check("G1: 满分是 12", float(score["max_score"]) == 12.0, score)
    context.check("G1: 答对 2 题，共 3 题",
                  (score["correct_count"], score["question_count"]) == ("2", "3"), score)

    detail = json.loads(score["detail"])
    context.check("G1: 明细逐题给出对错", detail["QPICK"]["correct"] is True
                  and detail["QMULTI"]["correct"] is False
                  and detail["QCITY"]["correct"] is True, detail)

    # -------------------------------------------------------------- G2 明细不含答案
    print("G2: 成绩明细里没有正确答案", file=sys.stderr)
    for needle in (TEXT_SENTINEL, TEXT_SENTINEL.lower(), "SQ003"):
        context.check("G2: 明细里没有 {}".format(needle), needle not in score["detail"], score["detail"])

    # -------------------------------------------------------------- G3 成绩不在答卷表
    print("G3: 成绩只在插件表里", file=sys.stderr)
    columns = [row[0].lower() for row in context.db.rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'lime_responses_{}'".format(sid))]
    context.check("G3: 答卷表里没有任何一列是分数",
                  not any(word in name for name in columns for word in ("score", "correct", "grade")),
                  columns)

    # -------------------------------------------------------------- G4 篡改
    print("G4: POST 里塞分数字段", file=sys.stderr)
    tampered = dict(answers)
    tampered[fields["QPICK"]] = WRONG  # 这次真的答错了
    context.respond(context.new_jar(), [
        {"get": start_url(sid)},
        {"submit": tampered, "move": "movesubmit",
         "extra": {"score": "100", "max_score": "100", "correct": "1",
                   "mjy_exam_score": "100", "correct_count": "3"}},
    ])
    ids = context.submitted_ids(sid)
    context.check("G4: 又交了一份卷", len(ids) == 2, ids)
    tampered_score = context.score_of(sid, ids[1])
    context.check("G4: 自称满分没有用，分数按服务端判的算",
                  tampered_score is not None and float(tampered_score["score"]) == 3.0,
                  tampered_score)
    context.check("G4: 第一份卷的成绩没有被这次提交改掉",
                  float(context.score_of(sid, ids[0])["score"]) == 8.0)

    # -------------------------------------------------------------- G5 不留两份
    print("G5: 一份卷子只有一份成绩", file=sys.stderr)
    rows = context.db.value(
        "SELECT COUNT(*) FROM lime_mjyruntimepolicy_exam_score "
        "WHERE survey_id = {} AND response_id = {}".format(sid, ids[0]))
    context.check("G5: 一份答卷只对应一行成绩", rows == "1", rows)

    # -------------------------------------------------------------- G6 没答的题
    print("G6: 一道都不答", file=sys.stderr)
    context.respond(context.new_jar(), [
        {"get": start_url(sid)},
        {"submit": {}, "move": "movesubmit"},
    ])
    ids = context.submitted_ids(sid)
    blank = context.score_of(sid, ids[2])
    context.check("G6: 空卷也判分", blank is not None)
    context.check("G6: 空卷 0 分", blank is not None and float(blank["score"]) == 0.0, blank)
    context.check("G6: 满分不因为没答而缩水",
                  blank is not None and float(blank["max_score"]) == 12.0, blank)
    context.check("G6: 明细里三道题都记成没答",
                  blank is not None and all(not item["answered"]
                                            for item in json.loads(blank["detail"]).values()),
                  blank)

    print(json.dumps({"failures": context.failures}))
    return 1 if context.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
