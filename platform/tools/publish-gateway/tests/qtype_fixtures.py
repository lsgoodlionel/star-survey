"""WP-02 题型测试共用的构造器与样例定义。"""

import json
from pathlib import Path

from pubgw.model import SurveyDefinition

from .fixtures import explicit_settings

QUESTION_TYPES_FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/surveys/question-types.json"


def fixture_payload():
    """发布到真引擎的那份全题型样例（v2）。"""
    return json.loads(QUESTION_TYPES_FIXTURE.read_text(encoding="utf-8"))


def fixture_definition():
    return SurveyDefinition.from_dict(fixture_payload())


def sub(code, text=None, scale=0, **extra):
    payload = {"uuid": "sq-{}-{}".format(code, scale), "code": code, "text": text or code}
    if scale:
        payload["scale"] = scale
    payload.update(extra)
    return payload


def answer(code, text=None, scale=0):
    payload = {"code": code, "text": text or code}
    if scale:
        payload["scale"] = scale
    return payload


def question(qtype, code="Q1", **extra):
    payload = {"uuid": "q-" + code, "code": code, "type": qtype, "text": "题目 " + code}
    payload.update(extra)
    return payload


def payload_with(*questions, version=1):
    return {
        "definitionVersion": version,
        "uuid": "def-qtypes",
        "title": "题型",
        "language": "en",
        "settings": explicit_settings(),
        "groups": [{"uuid": "g1", "title": "G1", "questions": list(questions)}],
    }


def definition_with(*questions, version=1):
    return SurveyDefinition.from_dict(payload_with(*questions, version=version))


def first_question(*questions, version=1):
    return definition_with(*questions, version=version).groups[0].questions[0]
