"""逻辑测试共用的 v2 定义：三个题组、覆盖各类可引用的题型。"""

import copy
import json
from pathlib import Path

from pubgw.model import SurveyDefinition

from .fixtures import explicit_settings

LOGIC_FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "surveys" / "publish-gateway-logic.json"
)


def logic_fixture_payload():
    return json.loads(LOGIC_FIXTURE.read_text(encoding="utf-8"))


def base_payload():
    """第一页：数值、单选（带其他）、多选、短文本、数组、双尺度、说明；第二、三页各一道文本题。"""
    return {
        "definitionVersion": 2,
        "uuid": "logic-0001",
        "title": "逻辑样例",
        "language": "en",
        "settings": explicit_settings(),
        "groups": [
            {
                "uuid": "g1",
                "title": "One",
                "questions": [
                    {"uuid": "q-age", "code": "QAGE", "type": "N", "text": "Age"},
                    {
                        "uuid": "q-pet",
                        "code": "QPET",
                        "type": "L",
                        "text": "Pet",
                        "other": True,
                        "answers": [{"code": "A1", "text": "Cat"}, {"code": "A2", "text": "Dog"}],
                    },
                    {
                        "uuid": "q-multi",
                        "code": "QMULTI",
                        "type": "M",
                        "text": "Multi",
                        "subquestions": [
                            {"uuid": "sq-m1", "code": "SQ001", "text": "One"},
                            {"uuid": "sq-m2", "code": "SQ002", "text": "Two"},
                        ],
                    },
                    {"uuid": "q-name", "code": "QNAME", "type": "S", "text": "Name"},
                    {
                        "uuid": "q-arr",
                        "code": "QARR",
                        "type": "F",
                        "text": "Array",
                        "subquestions": [{"uuid": "sq-a1", "code": "R1", "text": "Row"}],
                        "answers": [{"code": "Y1", "text": "Yes"}, {"code": "N1", "text": "No"}],
                    },
                    {
                        "uuid": "q-dual",
                        "code": "QDUAL",
                        "type": "1",
                        "text": "Dual",
                        "subquestions": [{"uuid": "sq-d1", "code": "R1", "text": "Row"}],
                        "answers": [
                            {"code": "L1", "text": "Left", "scale": 0},
                            {"code": "R9", "text": "Right", "scale": 1},
                        ],
                    },
                    {"uuid": "q-note", "code": "QNOTE", "type": "X", "text": "Note"},
                ],
            },
            {
                "uuid": "g2",
                "title": "Two",
                "questions": [{"uuid": "q-two", "code": "QTWO", "type": "S", "text": "Two"}],
            },
            {
                "uuid": "g3",
                "title": "Three",
                "questions": [{"uuid": "q-three", "code": "QTHREE", "type": "S", "text": "Three"}],
            },
        ],
    }


def question(payload, code):
    for group in payload["groups"]:
        for item in group["questions"]:
            if item["code"] == code:
                return item
    raise KeyError(code)


def with_changes(change):
    """复制 base_payload，交给 change(payload) 就地修改后解析成定义。"""
    payload = copy.deepcopy(base_payload())
    change(payload)
    return SurveyDefinition.from_dict(payload)


def with_condition(code, expression):
    return with_changes(lambda payload: question(payload, code).__setitem__("condition", expression))
