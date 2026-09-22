"""WP-02 题型的 LSS 编译：每个题型的金标准行、服务端规则、指纹稳定性，以及旧定义逐字节不变。"""

import datetime
import hashlib
import math
import re
import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.compiler import LssCompiler
from pubgw.fieldmap import binding_map, definition_signature, fingerprint, parse_fieldmap
from pubgw.questions.formats import FORMATS
from pubgw.verify import verify_publication

from .fixtures import fieldmap_for, sample_payload
from .qtype_fixtures import answer, definition_with, fixture_definition, question, sub

# 全题型样例的编译产物与结构指纹。改动它们说明发给引擎的东西变了——必须是有意的。
FIXTURE_LSS_SHA256 = "89bdeec0a4fa659fdc0c33ae491ace8f810fe1d638009b2c17cc97185252ac29"
FIXTURE_FINGERPRINT = "fm1:93bf4b726b65e22d"


def compile_lss(definition):
    return ElementTree.fromstring(LssCompiler().compile(definition).lss)


def rows_of(tree, section):
    node = tree.find(section)
    if node is None:
        return []
    return [{child.tag: (child.text or "") for child in row} for row in node.findall("rows/row")]


def attributes_of(tree, code="Q1"):
    qids = {row["title"]: row["qid"] for row in rows_of(tree, "questions")}
    return {row["attribute"]: row["value"] for row in rows_of(tree, "question_attributes") if row["qid"] == qids[code]}


# ------------------------------------------------------------------ 迷你求值器
# 把编译出的 ExpressionScript 规则按 PHP 语义在 Python 里跑一遍，用真实样本证明校验码算法正确。


def _php_regex(pattern, value):
    body = pattern[1:pattern.rindex("/")]
    return 1 if re.search(body, value) else 0


def _substr(value, start, length=None):
    start = int(start)
    return value[start:] if length is None else value[start:start + int(length)]


def _intval(value):
    match = re.match(r"-?\d+", str(value))
    return int(match.group(0)) if match else 0


def _strpos(haystack, needle):
    index = haystack.find(needle) if needle else -1
    return False if index < 0 else index


def _checkdate(month, day, year):
    try:
        datetime.date(int(year), int(month), int(day))
    except ValueError:
        return False
    return True


NAMESPACE = {
    "is_empty": lambda value: value in ("", None),
    "regexMatch": _php_regex,
    "strlen": len,
    "html_entity_decode": lambda value: value.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"),
    "substr": _substr,
    "intval": _intval,
    "sum": lambda *values: sum(values),
    "floor": math.floor,
    "strpos": _strpos,
    "strtoupper": lambda value: value.upper(),
    "checkdate": _checkdate,
    "count": lambda *values: sum(1 for value in values if value not in ("", None)),
    "iff": lambda condition, yes, no="": yes if condition else no,
}


def evaluate(expression, **variables):
    python = re.sub(r"\b([A-Z][A-Za-z0-9_]*)\.NAOK\b", lambda match: "V_" + match.group(1), expression)
    python = re.sub(r"\bif\(", "iff(", python)
    scope = dict(NAMESPACE)
    scope.update({"V_" + name: value for name, value in variables.items()})
    return bool(eval(python, {"__builtins__": {}}, scope))  # noqa: S307 - 只求值本模块编译出的规则


def format_rule(name):
    definition = definition_with(question("S", code="QX", format=name))
    return attributes_of(compile_lss(definition), "QX")["em_validation_q"]


class FormatRuleTest(unittest.TestCase):
    CASES = {
        "cn_mobile": (["13800138000", "19912345678"], ["12800138000", "1380013800", "138001380001", "1380013800a"]),
        "cn_postcode": (["100086"], ["10008", "1000860", "10008a"]),
        "email": (["a.b@example.cn"], ["a.b", "a@b", "a b@example.cn"]),
        "cn_id_card": (
            ["11010519491231002X", "11010519491231002x", "440308199901011234"],
            ["110105194912310021", "11010519491331002X", "01010519491231002X", "1101051949123100"],
        ),
        "cn_uscc": (
            ["91350100M000100Y43", "9111000071093123XX"],
            ["91350100M000100Y44", "91350100m000100Y43", "91350100I000100Y43", "91350100M000100Y4"],
        ),
    }

    def test_every_format_accepts_valid_and_rejects_invalid_samples(self):
        self.assertEqual(set(self.CASES), set(FORMATS))
        for name, (valid, invalid) in self.CASES.items():
            rule = format_rule(name)
            for value in valid:
                with self.subTest(format=name, value=value):
                    self.assertTrue(evaluate(rule, QX=value))
            for value in invalid:
                with self.subTest(format=name, value=value):
                    self.assertFalse(evaluate(rule, QX=value))

    def test_an_unanswered_question_passes_so_mandatory_stays_separate(self):
        for name in self.CASES:
            with self.subTest(format=name):
                self.assertTrue(evaluate(format_rule(name), QX=""))

    def test_golden_mobile_rule(self):
        self.assertEqual(
            '(is_empty(QX.NAOK) or regexMatch("/^1[3-9]' + "[0-9]" * 9 + '$/", html_entity_decode(QX.NAOK)))',
            format_rule("cn_mobile"),
        )

    def test_rules_never_contain_braces_or_backslashes(self):
        for name in FORMATS:
            with self.subTest(format=name):
                self.assertNotRegex(format_rule(name), r"[{}\\]")

    def test_each_format_carries_a_default_tip(self):
        definition = definition_with(question("S", code="QX", format="cn_id_card"))
        self.assertIn("身份证", attributes_of(compile_lss(definition), "QX")["em_validation_q_tip"])


class ServerRuleTest(unittest.TestCase):
    def test_max_length_writes_the_attribute_and_a_character_count_rule(self):
        attributes = attributes_of(compile_lss(definition_with(question("T", maxLength=5))))
        self.assertEqual("5", attributes["maximum_chars"])
        self.assertEqual("(is_empty(Q1.NAOK) or strlen(html_entity_decode(Q1.NAOK)) <= 5)", attributes["em_validation_q"])
        self.assertTrue(evaluate(attributes["em_validation_q"], Q1="中文五个字"))
        self.assertFalse(evaluate(attributes["em_validation_q"], Q1="中文六个字啊"))

    def test_exclusive_writes_exclude_all_others_and_a_server_rule(self):
        definition = definition_with(
            question("M", other=True, subquestions=[sub("S1"), sub("S2"), sub("SN", exclusive=True)])
        )
        attributes = attributes_of(compile_lss(definition))
        self.assertEqual("SN", attributes["exclude_all_others"])
        rule = attributes["em_validation_q"]
        self.assertEqual('(Q1_SN.NAOK != "Y" or count(Q1_S1.NAOK, Q1_S2.NAOK, Q1_other.NAOK) == 0)', rule)
        self.assertTrue(evaluate(rule, Q1_SN="Y", Q1_S1="", Q1_S2="", Q1_other=""))
        self.assertFalse(evaluate(rule, Q1_SN="Y", Q1_S1="Y", Q1_S2="", Q1_other=""))
        self.assertFalse(evaluate(rule, Q1_SN="Y", Q1_S1="", Q1_S2="", Q1_other="别的"))
        self.assertTrue(evaluate(rule, Q1_SN="", Q1_S1="Y", Q1_S2="Y", Q1_other=""))

    def test_two_exclusive_options_exclude_each_other(self):
        definition = definition_with(
            question("P", subquestions=[sub("S1"), sub("SA", exclusive=True), sub("SB", exclusive=True)])
        )
        attributes = attributes_of(compile_lss(definition))
        self.assertEqual("SA;SB", attributes["exclude_all_others"])
        self.assertFalse(evaluate(attributes["em_validation_q"], Q1_S1="", Q1_SA="Y", Q1_SB="Y"))

    def test_an_exclusive_option_with_nothing_to_exclude_gets_no_rule_or_tip(self):
        attributes = attributes_of(compile_lss(definition_with(question("M", subquestions=[sub("SN", exclusive=True)]))))
        self.assertEqual({"exclude_all_others": "SN"}, attributes)

    def test_format_is_and_ed_with_a_v2_validation_rule(self):
        definition = definition_with(
            question("S", format="cn_mobile", validation={"rule": "length(self) > 3", "message": "太短"}),
            version=2,
        )
        attributes = attributes_of(compile_lss(definition))
        rule = attributes["em_validation_q"]
        self.assertTrue(rule.startswith("((is_empty(Q1.NAOK) or (strlen(Q1.NAOK) > 3))) and ((is_empty(Q1.NAOK) or regexMatch("), rule)
        self.assertTrue(attributes["em_validation_q_tip"].startswith("太短；"))

    def test_format_is_and_ed_with_a_raw_v1_rule(self):
        definition = definition_with(
            question("S", format="cn_postcode", attributes={"em_validation_q": "Q1.NAOK != '000000'"})
        )
        rule = attributes_of(compile_lss(definition))["em_validation_q"]
        self.assertTrue(rule.startswith("(Q1.NAOK != '000000') and ("), rule)

    def test_questions_without_extensions_get_no_rules(self):
        attributes = attributes_of(compile_lss(definition_with(question("S", attributes={"maximum_chars": "9"}))))
        self.assertEqual({"maximum_chars": "9"}, attributes)


class GoldenRowsTest(unittest.TestCase):
    """全题型样例：逐题检查题型、主题、子题尺度与选项。"""

    @classmethod
    def setUpClass(cls):
        cls.definition = fixture_definition()
        cls.compiled = LssCompiler().compile(cls.definition)
        cls.tree = ElementTree.fromstring(cls.compiled.lss)
        cls.questions = {row["title"]: row for row in rows_of(cls.tree, "questions")}
        parents = {row["qid"]: row["title"] for row in rows_of(cls.tree, "questions")}
        cls.subquestions = {}
        for row in rows_of(cls.tree, "subquestions"):
            cls.subquestions.setdefault(parents[row["parent_qid"]], []).append((row["title"], row["scale_id"], row["type"]))
        cls.answers = {}
        for row in rows_of(cls.tree, "answers"):
            cls.answers.setdefault(parents[row["qid"]], []).append((row["code"], row["scale_id"]))

    def test_types_and_themes(self):
        expected = {
            "QCOMMENT": ("O", "list_with_comment"), "QYES": ("Y", "yesno"), "QGENDER": ("G", "gender"),
            "QNPS": ("L", "bootstrap_buttons"), "QSTAR": ("5", "5pointchoice"), "QIMG": ("L", "image_select-listradio"),
            "QCOL": ("H", "arrays/column"), "QA5": ("A", "arrays/5point"), "QA10": ("B", "arrays/10point"),
            "QYUN": ("C", "arrays/yesnouncertain"), "QISD": ("E", "arrays/increasesamedecrease"),
            "QMNUM": (":", "arrays/multiflexi"), "QMTXT": (";", "arrays/texts"), "QRANK": ("R", "ranking"),
            "QALLOC": ("K", "multiplenumeric"), "QMTEXT": ("Q", "multipleshorttext"), "QFILE": ("|", "file_upload"),
        }
        for code, (qtype, theme) in expected.items():
            with self.subTest(code=code):
                self.assertEqual((qtype, theme), (self.questions[code]["type"], self.questions[code]["question_theme_name"]))

    def test_matrix_columns_are_scale_one_subquestions(self):
        self.assertEqual(
            [("R1", "0", ":"), ("R2", "0", ":"), ("C1", "1", ":"), ("C2", "1", ":")], self.subquestions["QMNUM"]
        )

    def test_ranking_items_are_subquestions(self):
        self.assertEqual(["I1", "I2", "I3"], [code for code, _, _ in self.subquestions["QRANK"]])
        self.assertNotIn("QRANK", self.answers)

    def test_nps_answer_codes_are_zero_to_ten(self):
        self.assertEqual([str(value) for value in range(11)], [code for code, _ in self.answers["QNPS"]])

    def test_fixed_scale_arrays_carry_no_answers(self):
        for code in ("QA5", "QA10", "QYUN", "QISD", "QSTAR", "QYES", "QGENDER"):
            with self.subTest(code=code):
                self.assertNotIn(code, self.answers)

    def test_mandatory_flag(self):
        self.assertEqual("Y", self.questions["QMOBILE"]["mandatory"])

    def test_compiled_rules_stay_in_the_question_attributes(self):
        attributes = attributes_of(self.tree, "QIDCARD")
        self.assertIn("checkdate(", attributes["em_validation_q"])
        self.assertNotIn("format", attributes)

    def test_fixture_golden_lss(self):
        digest = hashlib.sha256(self.compiled.lss.encode("utf-8")).hexdigest()
        self.assertEqual(FIXTURE_LSS_SHA256, digest)

    def test_fixture_golden_fingerprint(self):
        self.assertEqual(FIXTURE_FINGERPRINT, self.compiled.fingerprint)


class FingerprintAndBindingTest(unittest.TestCase):
    def test_every_new_column_is_in_the_signature(self):
        signature = definition_signature(fixture_definition())
        for line in (":|QMNUM|R1_C2|0", "R|QRANK||0", "R|QRANK|3|0", "O|QCOMMENT|comment|0",
                     "||QFILE|filecount|0", "K|QALLOC|P3|0", "A|QA5|R1|0", "X|QNOTE||0"):
            with self.subTest(line=line):
                self.assertIn(line, signature)

    def test_text_edits_and_extension_keys_do_not_move_the_fingerprint(self):
        base = definition_with(question("S", code="QX"), question("M", code="QM", subquestions=[sub("S1"), sub("S2")]))
        changed = definition_with(
            question("S", code="QX", text="改题干", format="cn_mobile", maxLength=11),
            question("M", code="QM", subquestions=[sub("S1"), sub("S2", exclusive=True)]),
        )
        self.assertEqual(fingerprint(definition_signature(base)), fingerprint(definition_signature(changed)))

    def test_adding_a_matrix_column_moves_the_fingerprint(self):
        one = definition_with(question(":", subquestions=[sub("R1"), sub("C1", scale=1)]))
        two = definition_with(question(":", subquestions=[sub("R1"), sub("C1", scale=1), sub("C2", scale=1)]))
        self.assertNotEqual(fingerprint(definition_signature(one)), fingerprint(definition_signature(two)))

    def test_the_engine_fieldmap_verifies_and_binds_every_column(self):
        definition = fixture_definition()
        compiled = LssCompiler().compile(definition)
        rows = parse_fieldmap(fieldmap_for(definition))
        report = verify_publication(definition, compiled, rows)
        self.assertTrue(report.ok, report.issues)
        bound = {(item.code, field.aid, field.scale) for item in binding_map(definition, rows) for field in item.fields}
        self.assertIn(("QMNUM", "R2_C2", 0), bound)
        self.assertIn(("QRANK", "2", 0), bound)
        self.assertEqual(len(definition_signature(definition)), len(bound))

    def test_a_ranking_parent_row_without_aid_parses_as_the_json_column(self):
        rows = parse_fieldmap({"Q7": {"fieldname": "Q7", "type": "R", "qid": 7, "title": "QRANK"}})
        self.assertEqual(("", 0), rows[0].shape)


class LegacyStabilityTest(unittest.TestCase):
    def test_v1_sample_is_unchanged_by_the_type_extension(self):
        from .test_logic_compile import V1_SAMPLE_FINGERPRINT, V1_SAMPLE_LSS_SHA256
        from pubgw.model import SurveyDefinition

        compiled = LssCompiler().compile(SurveyDefinition.from_dict(sample_payload()))
        self.assertEqual(V1_SAMPLE_LSS_SHA256, hashlib.sha256(compiled.lss.encode("utf-8")).hexdigest())
        self.assertEqual(V1_SAMPLE_FINGERPRINT, compiled.fingerprint)

    def test_answers_and_other_are_untouched_for_existing_types(self):
        tree = compile_lss(definition_with(question("L", other=True, answers=[answer("A1"), answer("A2")])))
        self.assertEqual([], [row for row in rows_of(tree, "question_attributes")])


if __name__ == "__main__":
    unittest.main()
