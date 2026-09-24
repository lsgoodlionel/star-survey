"""答案键 → 引擎（WP-09.1，契约 survey-exam-v1 §4、§5）。

编译产物只有一样东西：一行 plugin_settings（`MjyRuntimePolicy` / `mjy_exam_key`）。
**没有第二个出口**——不进题目属性、不进选项的 assessment_value、不进任何表达式。

本文件里最要紧的不是"payload 长什么样"，而是 :class:`NoSecondOutletTest`：
它把整份 LSS 当成字符串扫一遍，除了那一行载荷以外任何地方出现答案都算失败。
真引擎那一侧的证据在 platform/tests/e2e/exam_key.py。
"""

import copy
import hashlib
import json
import unittest

from pubgw.compiler import LssCompiler
from pubgw.exam.compile import EXAM_KEY, PLUGIN_NAME, compile_exam
from pubgw.model import SurveyDefinition

from .exam_fixtures import TEXT_SENTINEL, exam_definition, exam_payload, full_exam, single_key
from .fixtures import sample_definition


def compiled(exam):
    return compile_exam(exam_definition(exam))


def lss_of(exam):
    return LssCompiler().compile(exam_definition(exam)).lss


class PayloadTest(unittest.TestCase):
    def test_definition_without_exam_compiles_to_nothing(self):
        self.assertIsNone(compile_exam(sample_definition()))

    def test_payload_is_canonical_json(self):
        text = compiled(full_exam()).payload
        self.assertEqual(text, json.dumps(json.loads(text), sort_keys=True,
                                          separators=(",", ":"), ensure_ascii=True))

    def test_payload_carries_the_schema_marker(self):
        self.assertEqual("mjy-exam-key/1", json.loads(compiled(full_exam()).payload)["schema"])

    def test_digest_is_the_sha256_of_the_payload(self):
        result = compiled(full_exam())
        self.assertEqual(hashlib.sha256(result.payload.encode("ascii")).hexdigest(), result.digest)

    def test_entries_are_sorted_by_question_code_so_the_digest_is_stable(self):
        forward = full_exam()
        backward = copy.deepcopy(forward)
        backward["answerKey"].reverse()
        self.assertEqual(compiled(forward).digest, compiled(backward).digest)

    def test_question_references_are_normalised_to_codes(self):
        by_uuid = compiled(single_key(question="q-single")).payload
        by_code = compiled(single_key(question="QSINGLE")).payload
        self.assertEqual(by_code, by_uuid)

    def test_entry_records_the_grading_kind(self):
        entries = {entry["question"]: entry for entry in json.loads(compiled(full_exam()).payload)["answerKey"]}
        self.assertEqual("choice", entries["QSINGLE"]["kind"])
        self.assertEqual("set", entries["QMULTI"]["kind"])
        self.assertEqual("text", entries["QTEXT"]["kind"])

    def test_text_matching_flags_are_explicit_in_the_payload(self):
        entry = json.loads(compiled(single_key(question="QTEXT", correct=["x"], ignoreCase=True)).payload)
        self.assertEqual({"ignoreCase": True, "trim": True}, entry["answerKey"][0]["match"])

    def test_non_ascii_answers_survive_the_ascii_encoding(self):
        payload = compiled(single_key(question="QTEXT", correct=["巴黎"])).payload
        self.assertEqual(["巴黎"], json.loads(payload)["answerKey"][0]["correct"])
        self.assertNotIn("巴黎", payload)  # 转义成 \uXXXX，仍然是纯 ASCII


class NoSecondOutletTest(unittest.TestCase):
    """答案只许出现在那一行 plugin_settings 里，整份 LSS 的其余部分都不许有。"""

    def setUp(self):
        self.exam = full_exam()
        self.lss = lss_of(self.exam)
        self.payload = compiled(self.exam).payload

    def without_the_payload(self):
        """挖掉那一行载荷，剩下的 LSS 里再出现答案就是第二个出口。"""
        self.assertIn(self.payload, self.lss, "载荷本该原样写进 LSS")
        return self.lss.replace(self.payload, "<<payload>>")

    def test_the_payload_is_the_only_place_the_text_answer_appears(self):
        self.assertNotIn(TEXT_SENTINEL, self.without_the_payload())

    def test_no_question_attribute_carries_the_answer_key(self):
        """题目属性会被主题直接渲染成 data-*（mjy_psych_trials 就是这样把正确按键
        送进页面的）。所以这一节里连一个与考试有关的属性名都不能有。"""
        attributes = self.without_the_payload().split("<question_attributes>")[1] \
            .split("</question_attributes>")[0]
        for marker in ("mjy_exam", "exam", "answerKey", "correct", "assessment"):
            self.assertNotIn(marker, attributes, "题目属性里出现了 {}".format(marker))

    def test_no_answer_option_is_marked_by_assessment_value(self):
        """引擎的 assessment_value 会随选项一起渲染。答对的那个选项不能比别人多带一个分。"""
        rest = self.without_the_payload()
        for row in rest.split("<row>"):
            if "<assessment_value>" in row:
                value = row.split("<assessment_value>")[1].split("</assessment_value>")[0]
                self.assertIn(value.strip(), ("", "0", "<![CDATA[0]]>"), row[:200])

    def test_the_exam_block_adds_no_question_and_no_equation(self):
        """计分表会追加计算值题（equation 属性会被引擎翻成页面 JS）。答案键一道题都不加。"""
        self.assertEqual(lss_of(None).count("<type>"), self.lss.count("<type>"))
        self.assertNotIn("equation", self.without_the_payload())

    def test_exactly_one_plugin_settings_row_holds_the_key(self):
        self.assertEqual(1, self.lss.count(EXAM_KEY))
        self.assertIn(PLUGIN_NAME, self.lss)

    def test_the_survey_row_is_unchanged_by_the_exam_block(self):
        """答案键不改任何引擎原生设置——它不是引擎能执行的东西。"""
        surveys = lambda text: text.split("<surveys>")[1].split("</surveys>")[0]
        self.assertEqual(surveys(lss_of(None)), surveys(self.lss))


if __name__ == "__main__":
    unittest.main()
