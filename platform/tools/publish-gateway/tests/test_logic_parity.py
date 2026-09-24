"""WP-03.4 双执行比对的平台侧：用例生成与分歧归责。

比对的两次执行是「平台解释 DSL」与「真引擎跑编译产物」。这里只测不依赖引擎的
那一半：同一份定义 × 同一组答案向量，生成成对的（DSL 源、编译产物、平台算出的值），
以及分歧时的归责规则。真引擎那一半在 platform/tests/e2e/publish_gateway_parity.py。
"""

import unittest

from pubgw.logic.parity import (
    BLAME_BOTH,
    BLAME_COMPILER,
    BLAME_NONE,
    BLAME_PLATFORM,
    BLAME_UNKNOWN,
    AnswerVector,
    adjudicate,
    build_cases,
)
from pubgw.model import SurveyDefinition

from .logic_fixtures import logic_fixture_payload

VECTOR_CAT = AnswerVector(
    name="cat",
    answers={"QAGE": "36", "QPET": "A1", "QNAME": "Tom", "QYEARS": "5"},
    hidden=("QDOGNAME",),
    expected={"groups[1].questions[2].calculation": "46"},
)
VECTOR_EMPTY = AnswerVector(name="empty", answers={})


def cases_for(*vectors):
    definition = SurveyDefinition.from_dict(logic_fixture_payload())
    return build_cases(definition, vectors)


def case_at(cases, path, vector="cat"):
    for case in cases:
        if case.path == path and case.vector == vector:
            return case
    raise KeyError("{} @ {}".format(path, vector))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.cases = cases_for(VECTOR_CAT, VECTOR_EMPTY)

    def test_every_expression_site_is_covered_for_every_vector(self):
        paths = {case.path for case in self.cases}
        self.assertIn("groups[1].condition", paths)
        self.assertIn("groups[1].questions[0].condition", paths)
        self.assertIn("groups[1].questions[1].validation.rule", paths)
        self.assertIn("groups[1].questions[2].calculation", paths)
        for path in paths:
            vectors = {case.vector for case in self.cases if case.path == path}
            self.assertEqual(vectors, {"cat", "empty"}, path)

    def test_a_case_carries_both_the_dsl_source_and_the_compiled_expression(self):
        case = case_at(self.cases, "groups[1].questions[0].condition")
        self.assertEqual(case.source, 'QPET == "A2"')
        self.assertEqual(case.compiled, '(QPET.NAOK == "A2")')

    def test_the_platform_side_is_already_evaluated_and_rendered(self):
        # 猫（A1）不是狗（A2），条件为假；引擎打印假是空串。
        self.assertEqual(case_at(self.cases, "groups[1].questions[0].condition").platform, "")
        self.assertEqual(
            case_at(self.cases, "groups[1].questions[2].calculation").platform, "46"
        )

    def test_a_hidden_question_reads_as_empty(self):
        # QDOGNAME 被条件隐藏，{{ QNAME }} 以外的引用读作空。
        case = case_at(self.cases, "groups[1].questions[3].calculation")
        self.assertEqual(case.source, 'join("Hi ", QNAME)')
        self.assertEqual(case.platform, "Hi Tom")

    def test_validation_rules_are_compared_in_their_engine_form(self):
        case = case_at(self.cases, "groups[1].questions[1].validation.rule")
        self.assertEqual(case.compiled, "(is_empty(QYEARS.NAOK) or (QYEARS.NAOK <= QAGE.NAOK))")
        self.assertEqual(case.platform, "1")

    def test_an_unanswered_question_passes_its_own_validation_rule(self):
        case = case_at(self.cases, "groups[1].questions[1].validation.rule", vector="empty")
        self.assertEqual(case.platform, "1")

    def test_each_placeholder_in_a_text_is_its_own_case(self):
        paths = [case.path for case in self.cases if case.path.startswith("groups[1].questions[4].text")]
        self.assertEqual(sorted(set(paths)), [
            "groups[1].questions[4].text{{0}}",
            "groups[1].questions[4].text{{1}}",
            "groups[1].questions[4].text{{2}}",
        ])

    def test_a_case_keeps_the_answers_and_hidden_questions_it_was_built_from(self):
        case = case_at(self.cases, "groups[1].questions[2].calculation")
        self.assertEqual(case.answers["QAGE"], "36")
        self.assertEqual(case.hidden, ("QDOGNAME",))

    def test_expected_values_ride_along_when_the_vector_declares_one(self):
        self.assertEqual(case_at(self.cases, "groups[1].questions[2].calculation").expected, "46")
        self.assertIsNone(case_at(self.cases, "groups[1].condition").expected)


class AdjudicationTest(unittest.TestCase):
    """分歧必须指出是哪一侧错了，而不是只报「不一致」。"""

    def setUp(self):
        self.case = cases_for(VECTOR_CAT)[0]

    def verdict(self, platform, engine, expected, errors=()):
        case = self.case.replace(platform=platform, expected=expected)
        return adjudicate(case, engine, list(errors))

    def test_agreement_blames_nobody(self):
        verdict = self.verdict("46", "46", None)
        self.assertTrue(verdict.agree)
        self.assertEqual(verdict.blame, BLAME_NONE)

    def test_an_engine_error_blames_the_compiler(self):
        verdict = self.verdict("46", "", None, errors=["Undefined variable"])
        self.assertFalse(verdict.agree)
        self.assertEqual(verdict.blame, BLAME_COMPILER)

    def test_the_expected_value_decides_who_is_wrong(self):
        self.assertEqual(self.verdict("45", "46", "46").blame, BLAME_PLATFORM)
        self.assertEqual(self.verdict("46", "45", "46").blame, BLAME_COMPILER)
        self.assertEqual(self.verdict("44", "45", "46").blame, BLAME_BOTH)

    def test_a_divergence_without_an_expected_value_is_unresolved(self):
        self.assertEqual(self.verdict("45", "46", None).blame, BLAME_UNKNOWN)

    def test_agreeing_on_the_wrong_value_still_blames_both(self):
        verdict = self.verdict("45", "45", "46")
        self.assertFalse(verdict.agree)
        self.assertEqual(verdict.blame, BLAME_BOTH)


if __name__ == "__main__":
    unittest.main()
