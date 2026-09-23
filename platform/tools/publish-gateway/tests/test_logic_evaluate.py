"""平台侧 DSL 解释器（WP-03.4 双执行比对的平台那一执行）。

这里的期望值全部照着 platform/contracts/survey-logic-dsl-v1.md 第 4 节写：
隐藏即空、算术空值传播、与空值比较的真假、文本引用先转义。
解释器只实现契约语义，**不模拟 PHP 的类型杂耍**——引擎在契约之外的怪异角落
正是双执行比对要暴露的东西，不能靠两边一起装傻把它盖住。
"""

import unittest

from pubgw.logic.evaluate import EMPTY, AnswerSheet, Interpreter, render
from pubgw.logic.model import LogicModel
from pubgw.logic.parser import parse_expression
from pubgw.model import SurveyDefinition

from .logic_fixtures import base_payload


def evaluate(source, owner=None, **answers):
    """按 base_payload 的定义求值；answers 的键是引擎变量名（QAGE、QMULTI_SQ001…）。"""
    hidden = answers.pop("_hidden", ())
    definition = SurveyDefinition.from_dict(base_payload())
    model = LogicModel(definition)
    question = model.scope.by_uuid.get(owner) if owner else None
    sheet = AnswerSheet(answers, frozenset(hidden))
    return Interpreter(model.scope, question).evaluate(parse_expression(source), sheet)


class LiteralTest(unittest.TestCase):
    def test_numbers_and_strings_are_their_own_value(self):
        self.assertEqual(evaluate("3"), 3)
        self.assertEqual(evaluate("3.5"), 3.5)
        self.assertEqual(evaluate('"hi"'), "hi")
        self.assertIs(evaluate("true"), True)
        self.assertIs(evaluate("false"), False)


class ReferenceTest(unittest.TestCase):
    def test_numeric_answer_reads_as_a_number(self):
        self.assertEqual(evaluate("QAGE", QAGE="36"), 36)

    def test_unanswered_question_is_empty(self):
        self.assertIs(evaluate("QAGE"), EMPTY)

    def test_hidden_question_reads_as_empty_even_when_it_has_a_value(self):
        # 契约 4「隐藏即空」：条件为假的题读作空串，而不是让表达式失效。
        self.assertIs(evaluate("QAGE", QAGE="36", _hidden=("QAGE",)), EMPTY)

    def test_single_choice_reads_the_answer_code(self):
        self.assertEqual(evaluate("QPET", QPET="A1"), "A1")

    def test_multiple_choice_member_is_a_boolean(self):
        self.assertIs(evaluate("QMULTI.SQ001", QMULTI_SQ001="Y"), True)
        self.assertIs(evaluate("QMULTI.SQ001", QMULTI_SQ001=""), False)

    def test_text_answer_is_escaped_the_way_the_engine_escapes_it(self):
        # 契约 4「引用转义」：htmlSpecialCharsUserValue 先转义 < > & { }。
        self.assertEqual(evaluate("QNAME", QNAME="<b>Tom</b>{QAGE}"), "&lt;b&gt;Tom&lt;/b&gt;&#123;QAGE&#125;")

    def test_choice_code_is_not_escaped(self):
        self.assertEqual(evaluate("QPET.other", QPET_other="a&b"), "a&amp;b")


class ArithmeticTest(unittest.TestCase):
    def test_sum_and_product(self):
        self.assertEqual(evaluate("QAGE + 4", QAGE="36"), 40)
        self.assertEqual(evaluate("QAGE * 2", QAGE="3"), 6)

    def test_empty_operand_propagates(self):
        self.assertIs(evaluate("QAGE + 4"), EMPTY)
        self.assertIs(evaluate("QAGE * 2", _hidden=("QAGE",), QAGE="3"), EMPTY)

    def test_division_by_a_non_literal_zero_is_empty(self):
        self.assertIs(evaluate("100 / QAGE", QAGE="0"), EMPTY)
        self.assertEqual(evaluate("100 / QAGE", QAGE="4"), 25)

    def test_unary_minus_propagates_empty(self):
        self.assertIs(evaluate("-QAGE"), EMPTY)
        self.assertEqual(evaluate("-QAGE", QAGE="7"), -7)


class ComparisonTest(unittest.TestCase):
    def test_numbers_compare_numerically(self):
        self.assertIs(evaluate("QAGE > 18", QAGE="36"), True)
        self.assertIs(evaluate("QAGE > 18", QAGE="7"), False)

    def test_comparison_with_an_empty_value_is_false(self):
        # 契约 4：与空值做 < <= > >= == 比较为假。
        for source in ("QAGE > 18", "QAGE < 18", "QAGE >= 18", "QAGE <= 18", "QAGE == 18"):
            self.assertIs(evaluate(source), False, source)

    def test_inequality_with_an_empty_value_is_true(self):
        self.assertIs(evaluate("QAGE != 18"), True)

    def test_choice_compares_against_an_answer_code(self):
        self.assertIs(evaluate('QPET == "A1"', QPET="A1"), True)
        self.assertIs(evaluate('QPET in ["A1", "A2"]', QPET="A2"), True)
        self.assertIs(evaluate('QPET in ["A1", "A2"]', QPET="-oth-"), False)

    def test_text_compares_lexicographically(self):
        self.assertIs(evaluate('QNAME < "b"', QNAME="a"), True)
        self.assertIs(evaluate('QNAME < "b"', QNAME="c"), False)

    def test_membership_in_a_multiple_choice_question(self):
        self.assertIs(evaluate('"SQ002" in QMULTI', QMULTI_SQ002="Y"), True)
        self.assertIs(evaluate('"SQ002" in QMULTI', QMULTI_SQ001="Y"), False)


class BooleanTest(unittest.TestCase):
    def test_and_or_not(self):
        self.assertIs(evaluate("QAGE > 10 and QAGE < 20", QAGE="15"), True)
        self.assertIs(evaluate("QAGE > 10 and QAGE < 20", QAGE="25"), False)
        self.assertIs(evaluate("QAGE > 10 or QAGE < 5", QAGE="3"), True)
        self.assertIs(evaluate("not QMULTI.SQ001", QMULTI_SQ001="Y"), False)


class FunctionTest(unittest.TestCase):
    def test_answered(self):
        self.assertIs(evaluate("answered(QAGE)", QAGE="0"), True)
        self.assertIs(evaluate("answered(QAGE)"), False)
        self.assertIs(evaluate("answered(QAGE)", QAGE="1", _hidden=("QAGE",)), False)

    def test_answered_on_a_multiple_choice_question(self):
        self.assertIs(evaluate("answered(QMULTI)", QMULTI_SQ002="Y"), True)
        self.assertIs(evaluate("answered(QMULTI)"), False)

    def test_count_of_a_set_and_of_a_list(self):
        self.assertEqual(evaluate("count(QMULTI)", QMULTI_SQ001="Y", QMULTI_SQ002="Y"), 2)
        self.assertEqual(evaluate("count(QMULTI)", QMULTI_SQ001="Y"), 1)
        self.assertEqual(evaluate("count(QAGE, QNAME)", QAGE="3"), 1)

    def test_sum_treats_unanswered_as_zero(self):
        # 契约 3：sum 未作答按 0，所以它永远不是空值。
        self.assertEqual(evaluate("sum(QAGE, 5)"), 5)
        self.assertEqual(evaluate("sum(QAGE, 5)", QAGE="4"), 9)

    def test_numeric_functions_propagate_empty(self):
        self.assertEqual(evaluate("abs(-3)"), 3)
        self.assertEqual(evaluate("round(3.456, 2)"), 3.46)
        self.assertEqual(evaluate("floor(3.9)"), 3)
        self.assertEqual(evaluate("ceil(3.1)"), 4)
        self.assertIs(evaluate("abs(QAGE)"), EMPTY)

    def test_if_picks_a_branch(self):
        self.assertEqual(evaluate('if(QAGE > 18, "old", "young")', QAGE="36"), "old")
        self.assertEqual(evaluate('if(QAGE > 18, "old", "young")'), "young")

    def test_coalesce_fills_in_the_default(self):
        self.assertEqual(evaluate("coalesce(QAGE, 0)"), 0)
        self.assertEqual(evaluate("coalesce(QAGE, 0)", QAGE="7"), 7)

    def test_length_counts_characters_of_the_escaped_text(self):
        self.assertEqual(evaluate("length(QNAME)", QNAME="abc"), 3)
        self.assertEqual(evaluate("length(QNAME)"), 0)

    def test_join_concatenates(self):
        self.assertEqual(evaluate('join("Hi ", QNAME)', QNAME="Tom"), "Hi Tom")
        self.assertEqual(evaluate('join("Hi ", QNAME)'), "Hi ")

    def test_label_gives_the_option_text(self):
        self.assertEqual(evaluate("label(QPET)", QPET="A1"), "Cat")
        self.assertEqual(evaluate("label(QPET)"), "")


class SelfTest(unittest.TestCase):
    def test_self_reads_the_owning_question(self):
        self.assertIs(evaluate("self <= 40", owner="q-age", QAGE="36"), True)
        self.assertIs(evaluate("self <= 40", owner="q-age", QAGE="41"), False)

    def test_self_on_an_unanswered_question_compares_false(self):
        self.assertIs(evaluate("self <= 40", owner="q-age"), False)


class RenderTest(unittest.TestCase):
    """两边比对用的规范化文本形式，必须和引擎打印出来的一致。"""

    def test_rendering_matches_the_engine(self):
        self.assertEqual(render(EMPTY), "")
        self.assertEqual(render(True), "1")
        self.assertEqual(render(False), "")
        self.assertEqual(render(3.0), "3")
        self.assertEqual(render(3.5), "3.5")
        self.assertEqual(render("hi"), "hi")


if __name__ == "__main__":
    unittest.main()
