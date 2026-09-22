"""AST → ExpressionScript 受限子集：金标准输出、转义、以及 LSS 里的落点。"""

import hashlib
import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.compiler import LssCompiler
from pubgw.fieldmap import definition_signature
from pubgw.logic.lower import LogicCompiler, lower_definition
from pubgw.model import DefinitionError, SurveyDefinition

from .fixtures import sample_definition, sample_payload
from .logic_fixtures import base_payload, logic_fixture_payload, question, with_changes

# 编译器改动前记下的 v1 产物：旧定义必须逐字节不变（指纹也不变）。
V1_SAMPLE_LSS_SHA256 = "9a8070853850c8a1bbe091ef6e5b8370711d54e9314179321521cb1f0b2af9d6"
V1_SAMPLE_FINGERPRINT = "fm1:09f7114b945f27b2"


def compile_expression(source, owner="q-two", kind="condition"):
    definition = SurveyDefinition.from_dict(base_payload())
    return LogicCompiler(definition).expression(source, kind, owner)


class GoldenExpressionTest(unittest.TestCase):
    CASES = (
        ("QAGE > 18", "(QAGE.NAOK > 18)"),
        ('QPET == "A1"', '(QPET.NAOK == "A1")'),
        ('QPET != "A1" and not answered(QNAME)', '((QPET.NAOK != "A1") and (!(!is_empty(QNAME.NAOK))))'),
        ('QPET in ["A1", "A2"]', '((QPET.NAOK == "A1") or (QPET.NAOK == "A2"))'),
        ('QPET.other == "x y"', '(QPET_other.NAOK == "x y")'),
        ("QMULTI.SQ001", '(QMULTI_SQ001.NAOK == "Y")'),
        ('"SQ002" in QMULTI', '(QMULTI_SQ002.NAOK == "Y")'),
        ("count(QMULTI) > 0", "(count(QMULTI_SQ001.NAOK, QMULTI_SQ002.NAOK) > 0)"),
        ("answered(QMULTI)", "(count(QMULTI_SQ001.NAOK, QMULTI_SQ002.NAOK) > 0)"),
        ('QARR.R1 == "Y1"', '(QARR_R1.NAOK == "Y1")'),
        ('QDUAL.R1[1] == "R9"', '(QDUAL_R1_1.NAOK == "R9")'),
        ('q("q-age") >= 3', "(QAGE.NAOK >= 3)"),
        ('q("sq-m2")', '(QMULTI_SQ002.NAOK == "Y")'),
        ('QNAME == "say \\"hi\\""', '(QNAME.NAOK == "say \\"hi\\"")'),
        ("true or false", "(1 or 0)"),
        # 算术：空值传播、除零得空，加法用 sum() 以免被引擎当成字符串拼接。
        ("QAGE + 1 > 2", '(if(is_empty(QAGE.NAOK), "", sum(QAGE.NAOK, 1)) > 2)'),
        (
            "QAGE / (QAGE - 1) > 2",
            '(if(is_empty(QAGE.NAOK) or ((QAGE.NAOK - 1)) == 0, "", (QAGE.NAOK / (QAGE.NAOK - 1))) > 2)',
        ),
        ("-QAGE < 0", '(if(is_empty(QAGE.NAOK), "", (-QAGE.NAOK)) < 0)'),
        ("round(QAGE / 3, 1) > 1", '(if(is_empty(QAGE.NAOK), "", round((QAGE.NAOK / 3), 1)) > 1)'),
        ("coalesce(QAGE, 0) * 2 > 1", "((if(is_empty(QAGE.NAOK), 0, QAGE.NAOK) * 2) > 1)"),
        ("sum(QAGE, 2) > 1", "(sum(QAGE.NAOK, 2) > 1)"),
        ('if(QAGE > 3, "a", "b") == "a"', '(if((QAGE.NAOK > 3), "a", "b") == "a")'),
        ("length(QNAME) < 5", "(strlen(QNAME.NAOK) < 5)"),
        ('join("Hi ", QNAME) == "Hi"', '(join("Hi ", QNAME.NAOK) == "Hi")'),
    )

    def test_golden_outputs(self):
        for source, expected in self.CASES:
            with self.subTest(source=source):
                self.assertEqual(expected, compile_expression(source))

    def test_output_never_contains_raw_braces_or_engine_fieldnames(self):
        for source, _ in self.CASES:
            compiled = compile_expression(source)
            self.assertNotIn("{", compiled)
            self.assertNotRegex(compiled, r"\d+X\d+X\d+")

    def test_validation_rule_passes_when_unanswered(self):
        compiled = compile_expression("self <= QAGE", owner="q-two", kind="validation")
        self.assertEqual("(is_empty(QTWO.NAOK) or (QTWO.NAOK <= QAGE.NAOK))", compiled)


class TemplateTest(unittest.TestCase):
    def compile(self, text, owner="q-two"):
        return LogicCompiler(SurveyDefinition.from_dict(base_payload())).template(text, owner)

    def test_literal_braces_are_neutralised(self):
        self.assertEqual("a &#123;QAGE&#125; b", self.compile("a {QAGE} b"))

    def test_placeholders_become_engine_substitutions(self):
        self.assertEqual("Hi {QNAME.NAOK}!", self.compile("Hi {{ QNAME }}!"))
        self.assertEqual("Pet {QPET.shown}", self.compile("Pet {{ label(QPET) }}"))
        self.assertEqual('{if(is_empty(QAGE.NAOK), "", sum(QAGE.NAOK, 1))}', self.compile("{{QAGE + 1}}"))

    def test_author_html_is_kept_but_expression_text_is_not(self):
        self.assertEqual("<b>Hi</b> &#123;&#125;", self.compile("<b>Hi</b> {}"))


class LoweringTest(unittest.TestCase):
    def test_v1_definitions_are_left_untouched(self):
        definition = sample_definition()
        self.assertIs(definition, lower_definition(definition))

    def test_v1_lss_is_byte_identical(self):
        compiled = LssCompiler().compile(sample_definition())
        self.assertEqual(V1_SAMPLE_LSS_SHA256, hashlib.sha256(compiled.lss.encode("utf-8")).hexdigest())
        self.assertEqual(V1_SAMPLE_FINGERPRINT, compiled.fingerprint)

    def test_v1_rejects_logic_fields(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["condition"] = "true"
        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_dict(payload)


def rows_of(tree, section):
    node = tree.find(section)
    if node is None:
        return []
    return [{child.tag: (child.text or "") for child in row} for row in node.findall("rows/row")]


class LogicFixtureLssTest(unittest.TestCase):
    """发布到真引擎的那份样例：检查每个落点。"""

    @classmethod
    def setUpClass(cls):
        cls.definition = SurveyDefinition.from_dict(logic_fixture_payload())
        cls.compiled = LssCompiler().compile(cls.definition)
        cls.tree = ElementTree.fromstring(cls.compiled.lss)

    def question_row(self, code):
        return next(row for row in rows_of(self.tree, "questions") if row["title"] == code)

    def attributes(self, code):
        qid = self.question_row(code)["qid"]
        return {row["attribute"]: row["value"] for row in rows_of(self.tree, "question_attributes") if row["qid"] == qid}

    def l10n(self, code):
        qid = self.question_row(code)["qid"]
        return next(row for row in rows_of(self.tree, "question_l10ns") if row["qid"] == qid)

    def test_relevance(self):
        self.assertEqual('(QPET.NAOK == "A2")', self.question_row("QDOGNAME")["relevance"])
        self.assertEqual("1", self.question_row("QAGE")["relevance"])
        self.assertEqual(
            "((!is_empty(QNAME.NAOK)) and (QSCORE.NAOK > 40))",
            self.question_row("QFEED")["relevance"],
        )
        groups = rows_of(self.tree, "groups")
        self.assertEqual(["1", '(QPET.NAOK != "A3")', "1"], [row["grelevance"] for row in groups])

    def test_calculated_values(self):
        self.assertEqual("*", self.question_row("QSCORE")["type"])
        self.assertEqual("equation", self.question_row("QSCORE")["question_theme_name"])
        self.assertEqual(
            {
                "equation": '{if(is_empty(QAGE.NAOK), "", sum(QAGE.NAOK, (if(is_empty(QYEARS.NAOK), 0, QYEARS.NAOK) * 2)))}',
                "hidden": "1",
                "numbers_only": "1",
            },
            self.attributes("QSCORE"),
        )
        self.assertEqual(
            {"equation": '{join("Hi ", QNAME.NAOK)}', "hidden": "1"},
            self.attributes("QGREET"),
        )

    def test_validation_attributes(self):
        self.assertEqual(
            {
                "em_validation_q": "(is_empty(QYEARS.NAOK) or (QYEARS.NAOK <= QAGE.NAOK))",
                "em_validation_q_tip": "Cannot be more than your age ({QAGE.NAOK}).",
            },
            self.attributes("QYEARS"),
        )

    def test_piping_and_literal_braces(self):
        self.assertEqual("What is your dog's name, {QNAME.NAOK}?", self.l10n("QDOGNAME")["question"])
        self.assertEqual("How many years have you had your {QPET.shown}?", self.l10n("QYEARS")["question"])
        self.assertEqual(
            "Your nickname (braces like &#123;this&#125; stay literal)", self.l10n("QNAME")["question"]
        )
        titles = rows_of(self.tree, "surveys_languagesettings")
        self.assertEqual("WP-03 logic sample &#123;not an expression&#125;", titles[0]["surveyls_title"])

    def test_signature_includes_calculated_columns(self):
        self.assertIn("*|QSCORE||0", definition_signature(self.definition))

    def test_compile_is_deterministic(self):
        self.assertEqual(self.compiled.lss, LssCompiler().compile(self.definition).lss)


class InvalidLogicDoesNotCompileTest(unittest.TestCase):
    def test_compile_refuses_invalid_logic(self):
        from pubgw.compiler import CompileError

        definition = with_changes(lambda p: question(p, "QTWO").__setitem__("condition", "QAGE >"))
        with self.assertRaises(CompileError):
            LssCompiler().compile(definition)
