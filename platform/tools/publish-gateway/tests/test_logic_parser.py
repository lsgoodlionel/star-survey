"""逻辑 DSL 的解析：词法、优先级、错误位置、模板占位符。"""

import unittest

from pubgw.logic import ast
from pubgw.logic.parser import (
    MAX_DEPTH,
    MAX_EXPRESSION_LENGTH,
    ExpressionSyntaxError,
    parse_expression,
    parse_template,
)


class LiteralTest(unittest.TestCase):
    def test_parses_numbers_strings_and_booleans(self):
        self.assertEqual(ast.Number("12.5", 0), parse_expression("12.5"))
        self.assertEqual(ast.String("a b", 0), parse_expression('"a b"'))
        self.assertEqual(ast.String("it's", 0), parse_expression("'it\\'s'"))
        self.assertEqual(ast.Bool(True, 0), parse_expression("true"))

    def test_rejects_an_unterminated_string(self):
        with self.assertRaises(ExpressionSyntaxError) as caught:
            parse_expression('QA == "abc')
        self.assertEqual(6, caught.exception.position)


class ReferenceTest(unittest.TestCase):
    def test_bare_identifier_is_a_question_code(self):
        self.assertEqual(ast.Ref("QAGE", False, None, None, 0), parse_expression("QAGE"))

    def test_q_function_references_by_uuid(self):
        node = parse_expression('q("77777777-0001")')
        self.assertEqual(ast.Ref("77777777-0001", True, None, None, 0), node)

    def test_member_and_scale(self):
        node = parse_expression("QDUAL.SQ001[1]")
        self.assertEqual(ast.Ref("QDUAL", False, "SQ001", 1, 0), node)

    def test_self_keyword(self):
        self.assertEqual(ast.SelfRef(0), parse_expression("self"))

    def test_engine_fieldnames_are_not_references(self):
        # SGQA 形状（123X45X678）不是合法的平台引用：永远不允许直写引擎字段名。
        with self.assertRaises(ExpressionSyntaxError):
            parse_expression("123X45X678 == 1")

    def test_engine_suffixes_are_rejected(self):
        with self.assertRaises(ExpressionSyntaxError):
            parse_expression("QAGE.NAOK.value")


class PrecedenceTest(unittest.TestCase):
    def test_multiplication_binds_tighter_than_addition(self):
        node = parse_expression("1 + 2 * 3")
        self.assertIsInstance(node, ast.Binary)
        self.assertEqual("+", node.op)
        self.assertEqual("*", node.right.op)

    def test_and_binds_tighter_than_or(self):
        node = parse_expression("a or b and c")
        self.assertEqual("or", node.op)
        self.assertEqual("and", node.right.op)

    def test_not_and_comparison(self):
        node = parse_expression("not QA > 3")
        self.assertIsInstance(node, ast.Unary)
        self.assertEqual("not", node.op)
        self.assertEqual(">", node.operand.op)

    def test_comparisons_do_not_chain(self):
        with self.assertRaises(ExpressionSyntaxError):
            parse_expression("1 < 2 < 3")

    def test_in_list(self):
        node = parse_expression('QP in ["A1", "A2"]')
        self.assertIsInstance(node, ast.InList)
        self.assertEqual(2, len(node.options))

    def test_function_call(self):
        node = parse_expression("coalesce(QA, 0)")
        self.assertEqual(ast.Call("coalesce", (ast.Ref("QA", False, None, None, 9), ast.Number("0", 13)), 0), node)


class RejectionTest(unittest.TestCase):
    def test_rejects_engine_operators_and_assignment(self):
        for source in ("QA = 1", "QA && QB", "QA || QB", "!QA", "QA eq 1", "{QA}", "QA; QB"):
            with self.subTest(source=source):
                with self.assertRaises(ExpressionSyntaxError):
                    parse_expression(source)

    def test_rejects_trailing_tokens_with_position(self):
        with self.assertRaises(ExpressionSyntaxError) as caught:
            parse_expression("QA == 1 QB")
        self.assertEqual(8, caught.exception.position)

    def test_rejects_empty_expression(self):
        with self.assertRaises(ExpressionSyntaxError):
            parse_expression("   ")

    def test_limits_length_and_depth(self):
        with self.assertRaises(ExpressionSyntaxError):
            parse_expression("1" * (MAX_EXPRESSION_LENGTH + 1))
        with self.assertRaises(ExpressionSyntaxError):
            parse_expression("(" * (MAX_DEPTH + 1) + "1" + ")" * (MAX_DEPTH + 1))


class TemplateTest(unittest.TestCase):
    def test_splits_literals_and_placeholders(self):
        parts = parse_template("Hi {{ QNAME }}, age {{QAGE}}!")
        self.assertEqual(
            (
                ast.Literal("Hi "),
                ast.Placeholder(ast.Ref("QNAME", False, None, None, 6), 3),
                ast.Literal(", age "),
                ast.Placeholder(ast.Ref("QAGE", False, None, None, 22), 20),
                ast.Literal("!"),
            ),
            parts,
        )

    def test_single_braces_are_literal(self):
        self.assertEqual((ast.Literal("a {b} c"),), parse_template("a {b} c"))

    def test_unterminated_placeholder(self):
        with self.assertRaises(ExpressionSyntaxError):
            parse_template("Hi {{ QNAME")

    def test_placeholder_error_position_is_absolute(self):
        with self.assertRaises(ExpressionSyntaxError) as caught:
            parse_template("ab {{ QA == }}")
        self.assertEqual(12, caught.exception.position)
