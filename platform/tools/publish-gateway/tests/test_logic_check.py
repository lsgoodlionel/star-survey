"""逻辑的静态检查：类型、引用、页面顺序、循环，全部以 ValidationIssue 报出。"""

import unittest

from pubgw.model import SurveyDefinition
from pubgw.validate import validate_definition

from .logic_fixtures import (
    base_payload,
    logic_fixture_payload,
    question,
    with_changes,
    with_condition,
)


def issues(definition):
    return validate_definition(definition).issues


def codes(definition):
    return sorted({issue.code for issue in issues(definition)})


class BaselineTest(unittest.TestCase):
    def test_base_payload_is_valid(self):
        self.assertEqual([], codes(SurveyDefinition.from_dict(base_payload())))

    def test_logic_fixture_is_valid(self):
        self.assertEqual([], codes(SurveyDefinition.from_dict(logic_fixture_payload())))

    def test_accepts_every_reference_shape(self):
        expressions = (
            "QAGE > 18 and QAGE <= 99",
            'QPET == "A1" or QPET == "-oth-"',
            'QPET in ["A1", "A2"]',
            'QPET.other == "hamster"',
            'QMULTI.SQ001 and not "SQ002" in QMULTI',
            "count(QMULTI) >= 1",
            'QARR.R1 == "Y1"',
            'QDUAL.R1[1] == "R9"',
            'q("q-age") > 3',
            'q("sq-a1") == "N1"',
            "answered(QNAME) and length(QNAME) < 10",
            "sum(QAGE, 1) > round(QAGE / 3, 1)",
            'if(QAGE > 3, "a", "b") == "a"',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual([], codes(with_condition("QTWO", expression)))


class SyntaxAndTypeTest(unittest.TestCase):
    def test_syntax_error_has_path_and_column(self):
        found = issues(with_condition("QTWO", "QAGE >"))
        self.assertEqual(["E_EXPR_SYNTAX"], [issue.code for issue in found])
        self.assertEqual("groups[1].questions[0].condition", found[0].path)
        self.assertIn("column 7", found[0].message)

    def test_condition_must_be_boolean(self):
        self.assertIn("E_EXPR_TYPE", codes(with_condition("QTWO", "QAGE + 1")))

    def test_numbers_do_not_compare_with_strings(self):
        self.assertIn("E_EXPR_TYPE", codes(with_condition("QTWO", 'QAGE == "3"')))

    def test_choice_does_not_compare_with_numbers(self):
        self.assertIn("E_EXPR_TYPE", codes(with_condition("QTWO", "QPET == 1")))

    def test_arithmetic_on_text_is_a_type_error(self):
        self.assertIn("E_EXPR_TYPE", codes(with_condition("QTWO", "QNAME * 2 > 1")))

    def test_boolean_operators_need_booleans(self):
        self.assertIn("E_EXPR_TYPE", codes(with_condition("QTWO", "QAGE and true")))

    def test_answer_code_must_exist(self):
        self.assertIn("E_EXPR_UNKNOWN_ANSWER_CODE", codes(with_condition("QTWO", 'QPET == "A9"')))
        self.assertIn("E_EXPR_UNKNOWN_ANSWER_CODE", codes(with_condition("QTWO", 'QPET in ["A1", "Z"]')))
        self.assertIn("E_EXPR_UNKNOWN_ANSWER_CODE", codes(with_condition("QTWO", 'QDUAL.R1[0] == "R9"')))

    def test_other_code_needs_other_enabled(self):
        definition = with_changes(
            lambda p: (
                question(p, "QPET").__setitem__("other", False),
                question(p, "QTWO").__setitem__("condition", 'QPET == "-oth-"'),
            )
        )
        self.assertIn("E_EXPR_UNKNOWN_ANSWER_CODE", codes(definition))

    def test_unknown_function_and_arity(self):
        self.assertIn("E_EXPR_UNKNOWN_FUNCTION", codes(with_condition("QTWO", "eval(QAGE) > 1")))
        self.assertIn("E_EXPR_ARITY", codes(with_condition("QTWO", "coalesce(QAGE) > 1")))

    def test_string_literals_cannot_carry_markup_or_braces(self):
        for literal in ('"<b>"', '"a&amp;b"', '"{QAGE}"', '"a\\\\b"'):
            with self.subTest(literal=literal):
                self.assertIn("E_EXPR_STRING_CHAR", codes(with_condition("QTWO", "QNAME == " + literal)))


class ReferenceTest(unittest.TestCase):
    def test_unknown_question(self):
        self.assertIn("E_EXPR_UNKNOWN_REFERENCE", codes(with_condition("QTWO", "QNOPE > 1")))
        self.assertIn("E_EXPR_UNKNOWN_REFERENCE", codes(with_condition("QTWO", 'q("nope") > 1')))

    def test_unknown_subquestion_and_bad_members(self):
        self.assertIn("E_EXPR_UNKNOWN_SUBQUESTION", codes(with_condition("QTWO", "QMULTI.SQ999")))
        self.assertIn("E_EXPR_UNKNOWN_SUBQUESTION", codes(with_condition("QTWO", '"SQ999" in QMULTI')))
        self.assertIn("E_EXPR_UNKNOWN_SUBQUESTION", codes(with_condition("QTWO", "QAGE.NAOK > 1")))

    def test_arrays_need_a_row_and_dual_scale_needs_a_scale(self):
        self.assertIn("E_EXPR_MEMBER_REQUIRED", codes(with_condition("QTWO", 'QARR == "Y1"')))
        self.assertIn("E_EXPR_SCALE_REQUIRED", codes(with_condition("QTWO", 'QDUAL.R1 == "L1"')))
        self.assertIn("E_EXPR_SCALE_REQUIRED", codes(with_condition("QTWO", 'QDUAL.R1[2] == "L1"')))

    def test_boilerplate_has_no_value(self):
        self.assertIn("E_EXPR_NOT_A_VALUE", codes(with_condition("QTWO", "answered(QNOTE)")))

    def test_self_only_in_validation(self):
        self.assertIn("E_EXPR_SELF_NOT_ALLOWED", codes(with_condition("QTWO", "answered(self)")))


class OrderTest(unittest.TestCase):
    def test_condition_on_a_later_page_is_rejected(self):
        found = issues(with_condition("QTWO", "answered(QTHREE)"))
        self.assertEqual(["E_EXPR_LATER_PAGE"], [issue.code for issue in found])
        self.assertIn("QTHREE", found[0].message)

    def test_forward_reference_on_the_same_page_is_rejected(self):
        self.assertEqual(["E_EXPR_FORWARD_REFERENCE"], codes(with_condition("QAGE", "answered(QNAME)")))

    def test_group_condition_cannot_look_into_its_own_page(self):
        definition = with_changes(lambda p: p["groups"][1].__setitem__("condition", "answered(QAGE)"))
        self.assertEqual([], codes(definition))
        definition = with_changes(lambda p: p["groups"][1].__setitem__("condition", "answered(QTHREE)"))
        self.assertEqual(["E_EXPR_LATER_PAGE"], codes(definition))

    def test_piping_a_later_question_is_rejected(self):
        definition = with_changes(lambda p: question(p, "QTWO").__setitem__("text", "Hi {{ QTHREE }}"))
        found = issues(definition)
        self.assertEqual(["E_EXPR_LATER_PAGE"], [issue.code for issue in found])
        self.assertEqual("groups[1].questions[0].text", found[0].path)

    def test_all_in_one_format_has_a_single_page(self):
        definition = with_changes(
            lambda p: (
                p["settings"].__setitem__("format", "A"),
                question(p, "QTWO").__setitem__("condition", "answered(QTHREE)"),
            )
        )
        self.assertEqual(["E_EXPR_FORWARD_REFERENCE"], codes(definition))


class CycleTest(unittest.TestCase):
    def test_self_condition_is_a_cycle(self):
        found = issues(with_condition("QTWO", "answered(QTWO)"))
        self.assertEqual(["E_EXPR_CYCLE"], [issue.code for issue in found])
        self.assertIn("QTWO -> QTWO", found[0].message)

    def test_calculations_that_depend_on_each_other(self):
        def change(payload):
            payload["groups"][1]["questions"] = [
                {"uuid": "c1", "code": "CA", "type": "*", "text": "a", "calculation": "CB + 1"},
                {"uuid": "c2", "code": "CB", "type": "*", "text": "b", "calculation": "CA + 1"},
            ]

        found = issues(with_changes(change))
        self.assertEqual(["E_EXPR_CYCLE"], sorted({issue.code for issue in found}))
        self.assertTrue(any("CA -> CB -> CA" in issue.message for issue in found))

    def test_group_condition_on_its_own_question_is_a_cycle(self):
        definition = with_changes(lambda p: p["groups"][1].__setitem__("condition", "answered(QTWO)"))
        self.assertEqual(["E_EXPR_CYCLE"], codes(definition))


class CalculationAndValidationTest(unittest.TestCase):
    def test_calculated_value_needs_a_calculation(self):
        definition = with_changes(
            lambda p: p["groups"][1]["questions"].append({"uuid": "c", "code": "CALC", "type": "*", "text": "c"})
        )
        self.assertIn("E_CALCULATION_MISSING", codes(definition))

    def test_calculation_only_on_calculated_type(self):
        definition = with_changes(lambda p: question(p, "QTWO").__setitem__("calculation", "1"))
        self.assertIn("E_CALCULATION_UNEXPECTED", codes(definition))

    def test_calculation_cannot_be_mandatory_or_boolean(self):
        def change(payload):
            payload["groups"][1]["questions"].append(
                {"uuid": "c", "code": "CALC", "type": "*", "text": "c", "mandatory": True, "calculation": "QAGE > 1"}
            )

        found = codes(with_changes(change))
        self.assertIn("E_CALCULATION_MANDATORY", found)
        self.assertIn("E_EXPR_TYPE", found)

    def test_calculated_type_flows_into_references(self):
        def change(payload):
            payload["groups"][1]["questions"] = [
                {"uuid": "c", "code": "CALC", "type": "*", "text": "c", "calculation": "QAGE * 2"},
                {"uuid": "t", "code": "QT", "type": "S", "text": "t", "condition": 'CALC == "x"'},
            ]

        self.assertIn("E_EXPR_TYPE", codes(with_changes(change)))

    def test_validation_rule(self):
        valid = with_changes(
            lambda p: question(p, "QTWO").__setitem__(
                "validation", {"rule": "length(self) <= QAGE", "message": "Too long for {{ QAGE }}"}
            )
        )
        self.assertEqual([], codes(valid))
        unsupported = with_changes(
            lambda p: question(p, "QMULTI").__setitem__("validation", {"rule": "true", "message": "x"})
        )
        self.assertIn("E_VALIDATION_UNSUPPORTED_TYPE", codes(unsupported))


class RawEngineExpressionTest(unittest.TestCase):
    def test_v2_rejects_raw_relevance_and_expression_attributes(self):
        definition = with_changes(
            lambda p: (
                question(p, "QTWO").__setitem__("relevance", "QAGE.NAOK > 1"),
                question(p, "QNAME").__setitem__("attributes", {"em_validation_q": "1"}),
                p["groups"][2].__setitem__("relevance", "1 == 1"),
            )
        )
        found = issues(definition)
        self.assertEqual({"E_RAW_EXPRESSION"}, {issue.code for issue in found})
        self.assertEqual(3, len(found))
