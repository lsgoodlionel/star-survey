"""逻辑与发布链路的衔接：兼容性、422 失败格式、v2 样例在假引擎上完整走通。"""

import hashlib
import unittest

from pubgw.compiler import LssCompiler
from pubgw.model import SurveyDefinition
from pubgw.publish import Publisher
from pubgw.rpc import RemoteControlClient
from pubgw.validate import validate_definition

from .fakes import FakeEngine
from .fixtures import sample_payload
from .logic_fixtures import logic_fixture_payload, question, with_changes, with_condition

# v1 定义里的 relevance 原样直通、文本里的花括号不转义：这是 v1 的既有行为，必须保持。
# 该值取自改动前（HEAD d3c22b40）的编译器。
V1_RAW_RELEVANCE_LSS_SHA256 = "f40f3db8943c98ffef1e0f8ca83928107c1a9ca04c032d6dd8793eaafb0625b2"


def publisher_for(engine):
    client = RemoteControlClient(engine.transport)
    client.login("admin", "password")
    return Publisher(client, engine_instance="survey-test-web")


def v1_with_raw_relevance():
    payload = sample_payload()
    payload["groups"][0]["questions"][1]["relevance"] = "QSINGLE.NAOK == 'A1'"
    payload["groups"][0]["questions"][1]["text"] = "Echo {QSINGLE}"
    return SurveyDefinition.from_dict(payload)


class CompatibilityTest(unittest.TestCase):
    def test_v1_raw_relevance_still_compiles_byte_identically(self):
        lss = LssCompiler().compile(v1_with_raw_relevance()).lss
        self.assertIn("<![CDATA[QSINGLE.NAOK == 'A1']]>", lss)
        self.assertIn("<![CDATA[Echo {QSINGLE}]]>", lss)
        self.assertEqual(V1_RAW_RELEVANCE_LSS_SHA256, hashlib.sha256(lss.encode("utf-8")).hexdigest())

    def test_v1_cannot_declare_a_calculated_value(self):
        payload = sample_payload()
        payload["groups"][0]["questions"].append({"uuid": "c", "code": "CALC", "type": "*", "text": "c"})
        report = validate_definition(SurveyDefinition.from_dict(payload))
        self.assertIn("E_CALCULATION_MISSING", {issue.code for issue in report.issues})


class ExtraCheckTest(unittest.TestCase):
    def test_division_by_literal_zero(self):
        codes = {issue.code for issue in validate_definition(with_condition("QTWO", "QAGE / 0 > 1")).issues}
        self.assertEqual({"E_EXPR_DIVISION_BY_ZERO"}, codes)

    def test_other_member_of_multiple_choice(self):
        definition = with_changes(
            lambda p: (
                question(p, "QMULTI").__setitem__("other", True),
                question(p, "QTWO").__setitem__("condition", 'count(QMULTI) > 1 and QMULTI.other != "x"'),
            )
        )
        self.assertEqual((), validate_definition(definition).issues)

    def test_every_issue_names_a_path(self):
        definition = with_condition("QTWO", 'QNOPE == 1 or QPET == "A9" or answered(QTHREE)')
        found = validate_definition(definition).issues
        self.assertEqual(
            ["E_EXPR_UNKNOWN_REFERENCE", "E_EXPR_UNKNOWN_ANSWER_CODE", "E_EXPR_LATER_PAGE"],
            [issue.code for issue in found],
        )
        self.assertTrue(all(issue.path == "groups[1].questions[0].condition" for issue in found))


class PublishTest(unittest.TestCase):
    def test_invalid_logic_is_a_validate_failure_and_never_reaches_the_engine(self):
        definition = with_condition("QTWO", "answered(QTWO)")
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertEqual("validate", result.failed_stage)
        self.assertEqual(["get_session_key"], engine.methods())
        self.assertEqual(1, len(result.failures))
        self.assertTrue(result.failures[0].startswith("E_EXPR_CYCLE groups[1].questions[0].condition: "))

    def test_logic_fixture_publishes_end_to_end_on_the_fake_engine(self):
        definition = SurveyDefinition.from_dict(logic_fixture_payload())
        engine = FakeEngine(definition)

        result = publisher_for(engine).publish(definition)

        self.assertTrue(result.ok, result.failures)
        codes = [binding.code for binding in result.binding.questions]
        self.assertIn("QSCORE", codes)
