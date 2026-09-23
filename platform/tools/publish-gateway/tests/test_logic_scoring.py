"""WP-03.3 计分：选项得分 → 总分 → 分段 → 按分段展示结果。

计分不是第二套编译器：它只把作者写的计分表展开成 v2 的 DSL（计算值题＋条件），
再交给 03.1／03.2 已有的解析、类型检查、依赖检查与 emit 走完全程。
"""

import copy
import json
import unittest
from dataclasses import replace

from pubgw.logic.lower import lower_definition
from pubgw.logic.scoring import ScoringError, expand_scoring
from pubgw.model import DefinitionError, Score, ScoreItem, SurveyDefinition
from pubgw.validate import validate_definition

from .logic_fixtures import base_payload, question


def payload_with_scoring(scoring):
    payload = copy.deepcopy(base_payload())
    payload["scoring"] = scoring
    return payload


SIMPLE_SCORE = {
    "uuid": "score-1",
    "code": "STOTAL",
    "title": "总分",
    "items": [
        {"question": "QPET", "points": {"A1": 3, "A2": 5}},
        {"question": "QMULTI", "points": {"SQ001": 2}},
        {"question": "QAGE", "weight": 2},
    ],
    "bands": [
        {"code": "LOW", "upTo": 5, "text": "得分偏低"},
        {"code": "HIGH", "text": "得分很高"},
    ],
}


def definition_with(scoring):
    return SurveyDefinition.from_dict(payload_with_scoring(scoring))


def issue_codes(definition):
    return [issue.code for issue in validate_definition(definition).issues]


def generated(definition):
    """展开后新增的那一组题（计分组永远追加在最后）。"""
    return expand_scoring(definition).groups[-1]


def question_by_code(group, code):
    for question in group.questions:
        if question.code == code:
            return question
    raise KeyError(code)


class StructureTest(unittest.TestCase):
    def test_scoring_requires_definition_version_2(self):
        payload = payload_with_scoring([SIMPLE_SCORE])
        payload["definitionVersion"] = 1
        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_dict(payload)

    def test_scoring_must_be_a_list_of_objects(self):
        with self.assertRaises(DefinitionError):
            definition_with({"code": "STOTAL"})

    def test_a_score_needs_a_code_and_at_least_one_item(self):
        with self.assertRaises(DefinitionError):
            definition_with([{"uuid": "s", "title": "t", "items": []}])

    def test_points_must_be_numbers(self):
        with self.assertRaises(DefinitionError):
            definition_with([dict(SIMPLE_SCORE, items=[{"question": "QPET", "points": {"A1": "3"}}])])


class ExpansionTest(unittest.TestCase):
    def test_the_total_compiles_into_a_calculated_question(self):
        total = question_by_code(generated(definition_with([SIMPLE_SCORE])), "STOTAL")
        self.assertEqual(total.type, "*")
        self.assertEqual(
            total.calculation,
            'sum(if(QPET == "A1", 3, 0), if(QPET == "A2", 5, 0), if(QMULTI.SQ001, 2, 0), coalesce(QAGE, 0) * 2)',
        )

    def test_an_array_row_item_scores_that_row(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QARR", "member": "R1", "points": {"Y1": 4}}])
        total = question_by_code(generated(definition_with([score])), "STOTAL")
        self.assertEqual(total.calculation, 'sum(if(QARR.R1 == "Y1", 4, 0))')

    def test_a_question_can_be_named_by_uuid(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "q-age", "weight": 1}])
        total = question_by_code(generated(definition_with([score])), "STOTAL")
        self.assertEqual(total.calculation, 'sum(coalesce(q("q-age"), 0) * 1)')

    def test_bands_compile_into_a_nested_if_over_the_total(self):
        band = question_by_code(generated(definition_with([SIMPLE_SCORE])), "STOTALB")
        self.assertEqual(band.calculation, 'if(STOTAL <= 5, "LOW", "HIGH")')

    def test_three_bands_nest_in_order(self):
        score = dict(SIMPLE_SCORE, bands=[
            {"code": "LOW", "upTo": 5},
            {"code": "MID", "upTo": 10},
            {"code": "HIGH"},
        ])
        band = question_by_code(generated(definition_with([score])), "STOTALB")
        self.assertEqual(band.calculation, 'if(STOTAL <= 5, "LOW", if(STOTAL <= 10, "MID", "HIGH"))')

    def test_each_band_text_becomes_a_display_question_shown_only_for_that_band(self):
        group = generated(definition_with([SIMPLE_SCORE]))
        low = question_by_code(group, "STOTALR1")
        self.assertEqual(low.type, "X")
        self.assertEqual(low.text, "得分偏低")
        self.assertEqual(low.condition, 'STOTALB == "LOW"')

    def test_bands_without_text_produce_no_display_question(self):
        score = dict(SIMPLE_SCORE, bands=[{"code": "LOW", "upTo": 5}, {"code": "HIGH"}])
        codes = [question.code for question in generated(definition_with([score])).questions]
        self.assertEqual(codes, ["STOTAL", "STOTALB"])

    def test_the_scoring_group_carries_no_condition_so_scores_are_never_cleared(self):
        self.assertEqual(generated(definition_with([SIMPLE_SCORE])).condition, "")

    def test_a_definition_without_scoring_is_returned_untouched(self):
        definition = SurveyDefinition.from_dict(base_payload())
        self.assertIs(expand_scoring(definition), definition)


class ValidationTest(unittest.TestCase):
    def test_a_well_formed_score_validates(self):
        self.assertEqual(issue_codes(definition_with([SIMPLE_SCORE])), [])

    def test_unknown_question(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QNOPE", "weight": 1}])
        self.assertIn("E_SCORING_UNKNOWN_QUESTION", issue_codes(definition_with([score])))

    def test_unknown_answer_code(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QPET", "points": {"ZZ": 1}}])
        self.assertIn("E_SCORING_UNKNOWN_KEY", issue_codes(definition_with([score])))

    def test_unknown_subquestion_of_a_multiple_choice_question(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QMULTI", "points": {"SQ999": 1}}])
        self.assertIn("E_SCORING_UNKNOWN_KEY", issue_codes(definition_with([score])))

    def test_a_numeric_question_needs_a_weight_not_points(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QAGE", "points": {"A1": 1}}])
        self.assertIn("E_SCORING_ITEM_SHAPE", issue_codes(definition_with([score])))

    def test_a_choice_question_needs_points_not_a_weight(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QPET", "weight": 2}])
        self.assertIn("E_SCORING_ITEM_SHAPE", issue_codes(definition_with([score])))

    def test_a_display_question_cannot_be_scored(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "QNOTE", "weight": 1}])
        self.assertIn("E_SCORING_ITEM_SHAPE", issue_codes(definition_with([score])))

    def test_the_score_code_may_not_collide_with_a_question(self):
        score = dict(SIMPLE_SCORE, code="QAGE")
        self.assertIn("E_SCORING_CODE_CONFLICT", issue_codes(definition_with([score])))

    def test_two_scores_may_not_share_a_code(self):
        second = dict(SIMPLE_SCORE, uuid="score-2")
        self.assertIn("E_SCORING_CODE_CONFLICT", issue_codes(definition_with([SIMPLE_SCORE, second])))

    def test_the_score_code_must_be_a_legal_question_code(self):
        self.assertIn("E_SCORING_CODE", issue_codes(definition_with([dict(SIMPLE_SCORE, code="1bad")])))

    def test_a_score_code_that_is_too_long_leaves_no_room_for_the_band_question(self):
        self.assertIn("E_SCORING_CODE", issue_codes(definition_with([dict(SIMPLE_SCORE, code="S" * 17)])))

    def test_band_codes_must_be_answer_code_shaped(self):
        score = dict(SIMPLE_SCORE, bands=[{"code": "a b", "upTo": 1}, {"code": "HIGH"}])
        self.assertIn("E_SCORING_BAND_CODE", issue_codes(definition_with([score])))

    def test_exactly_the_last_band_may_omit_its_upper_bound(self):
        score = dict(SIMPLE_SCORE, bands=[{"code": "LOW"}, {"code": "HIGH", "upTo": 5}])
        self.assertIn("E_SCORING_BAND_ORDER", issue_codes(definition_with([score])))

    def test_band_bounds_must_increase(self):
        score = dict(SIMPLE_SCORE, bands=[
            {"code": "LOW", "upTo": 10},
            {"code": "MID", "upTo": 5},
            {"code": "HIGH"},
        ])
        self.assertIn("E_SCORING_BAND_ORDER", issue_codes(definition_with([score])))

    def test_a_single_open_band_is_pointless_but_legal(self):
        score = dict(SIMPLE_SCORE, bands=[{"code": "ALL", "text": "结果"}])
        self.assertEqual(issue_codes(definition_with([score])), [])

    def test_bands_are_optional(self):
        score = {key: value for key, value in SIMPLE_SCORE.items() if key != "bands"}
        self.assertEqual(issue_codes(definition_with([score])), [])
        codes = [question.code for question in generated(definition_with([score])).questions]
        self.assertEqual(codes, ["STOTAL"])


class EscapingTest(unittest.TestCase):
    """作者写的计分文本永远不能变成可执行表达式（03.2 的做法，计分沿用）。"""

    HOSTILE = '结果 {QAGE} 与 {{ QAGE }}'

    def test_band_text_braces_are_escaped_and_never_executed(self):
        score = dict(SIMPLE_SCORE, bands=[
            {"code": "LOW", "upTo": 5, "text": self.HOSTILE},
            {"code": "HIGH", "text": "高"},
        ])
        definition = expand_scoring(definition_with([score]))
        lowered = lower_definition(definition)
        text = question_by_code(lowered.groups[-1], "STOTALR1").text
        # 作者写的 {QAGE} 被转义成实体，只有 {{ }} 里的才编译成引擎表达式。
        self.assertIn("&#123;QAGE&#125;", text)
        self.assertIn("{QAGE.NAOK}", text)

    def test_the_score_title_is_escaped_too(self):
        definition = expand_scoring(definition_with([dict(SIMPLE_SCORE, title="分数 {QAGE}")]))
        lowered = lower_definition(definition)
        self.assertEqual(question_by_code(lowered.groups[-1], "STOTAL").text, "分数 &#123;QAGE&#125;")


class CompilationTest(unittest.TestCase):
    def test_the_total_compiles_to_the_expected_expression_script(self):
        definition = expand_scoring(definition_with([SIMPLE_SCORE]))
        total = question_by_code(lower_definition(definition).groups[-1], "STOTAL")
        self.assertEqual(
            total.attributes["equation"],
            '{sum(if((QPET.NAOK == "A1"), 3, 0), if((QPET.NAOK == "A2"), 5, 0),'
            ' if((QMULTI_SQ001.NAOK == "Y"), 2, 0), (if(is_empty(QAGE.NAOK), 0, QAGE.NAOK) * 2))}',
        )
        self.assertEqual(total.attributes["numbers_only"], "1")
        self.assertEqual(total.attributes["hidden"], "1")

    def test_the_band_question_compiles_to_text(self):
        definition = expand_scoring(definition_with([SIMPLE_SCORE]))
        band = question_by_code(lower_definition(definition).groups[-1], "STOTALB")
        self.assertEqual(band.attributes["equation"], '{if((STOTAL.NAOK <= 5), "LOW", "HIGH")}')
        self.assertNotIn("numbers_only", band.attributes)

    def test_authors_can_branch_on_the_score_from_ordinary_logic(self):
        payload = payload_with_scoring([SIMPLE_SCORE])
        payload["groups"][2]["questions"][0]["condition"] = "STOTAL > 4"
        definition = SurveyDefinition.from_dict(payload)
        # 计分题追加在最后一组，第三组的题引用它属于「向后引用」，必须被挡下。
        self.assertIn("E_EXPR_LATER_PAGE", issue_codes(definition))


class ReferenceRenderingTest(unittest.TestCase):
    """计分把题目引用插进生成的 DSL 文本，插进去的东西必须是受限字符集。

    定性：**不是提权**。生成的文本会被标准 v2 解析器重新解析，走同样的类型检查、
    引用检查与环检测，作者本来就能直接写 v2 DSL 表达式。问题是正确性（校验的是原始
    引用，渲染出来的可能指向另一道题）和不变式（契约 §6 与本模块顶部都声称生成的
    表达式里没有一处作者自由文本）。根治在 model.py 的解析期字符集；这里是纵深防御。
    """

    HOSTILE = 'X") + 999, 0) * 1, sum(1'

    def test_a_hostile_uuid_never_gets_as_far_as_expansion(self):
        payload = payload_with_scoring([dict(SIMPLE_SCORE, items=[{"question": self.HOSTILE, "weight": 1}])])
        question(payload, "QAGE")["uuid"] = self.HOSTILE
        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_dict(payload)

    def test_expansion_refuses_a_reference_it_cannot_render_safely(self):
        # 绕过解析期（直接造 dataclass），展开必须自己也挡住，而不是原样拼进表达式。
        hostile = Score(
            uuid="score-x", code="STOTAL", title="总分",
            items=(ScoreItem(question=self.HOSTILE, weight=1.0),),
        )
        definition = replace(SurveyDefinition.from_dict(base_payload()), scoring=(hostile,))
        with self.assertRaises(ScoringError):
            expand_scoring(definition)

    def test_a_safe_uuid_still_renders_as_a_uuid_reference(self):
        score = dict(SIMPLE_SCORE, items=[{"question": "q-age", "weight": 1}])
        total = question_by_code(generated(definition_with([score])), "STOTAL")
        self.assertEqual(total.calculation, 'sum(coalesce(q("q-age"), 0) * 1)')


class GroupTitleTest(unittest.TestCase):
    """多份计分表共用一个计分组，标题冲突必须报错，不能静默丢掉一个。"""

    def test_two_scores_with_different_group_titles_are_rejected(self):
        first = dict(SIMPLE_SCORE, groupTitle="结果甲")
        second = dict(SIMPLE_SCORE, uuid="score-2", code="SOTHER", groupTitle="结果乙")
        self.assertIn("E_SCORING_GROUP_TITLE", issue_codes(definition_with([first, second])))

    def test_two_scores_sharing_a_group_title_expand_into_one_group(self):
        first = dict(SIMPLE_SCORE, groupTitle="结果")
        second = dict(SIMPLE_SCORE, uuid="score-2", code="SOTHER", groupTitle="结果")
        definition = definition_with([first, second])
        self.assertEqual(issue_codes(definition), [])
        group = generated(definition)
        self.assertEqual(group.title, "结果")
        codes = [item.code for item in group.questions]
        self.assertIn("STOTAL", codes)
        self.assertIn("SOTHER", codes)
        self.assertIn("SOTHERB", codes)

    def test_scores_that_both_leave_the_group_title_default_agree(self):
        second = dict(SIMPLE_SCORE, uuid="score-2", code="SOTHER")
        score = {key: value for key, value in SIMPLE_SCORE.items() if key != "groupTitle"}
        self.assertEqual(issue_codes(definition_with([score, second])), [])


class NonFiniteNumberTest(unittest.TestCase):
    """Infinity／NaN 是 json.loads 默认认的字面量，进了表达式会被当成标识符引用。"""

    def test_non_finite_points_are_rejected(self):
        for value in (float("inf"), float("-inf"), float("nan")):
            payload = payload_with_scoring(
                [dict(SIMPLE_SCORE, items=[{"question": "QPET", "points": {"A1": value}}])]
            )
            with self.assertRaises(DefinitionError, msg=repr(value)):
                SurveyDefinition.from_dict(payload)

    def test_non_finite_weight_and_band_bound_are_rejected(self):
        with self.assertRaises(DefinitionError):
            definition_with([dict(SIMPLE_SCORE, items=[{"question": "QAGE", "weight": float("inf")}])])
        with self.assertRaises(DefinitionError):
            definition_with([dict(SIMPLE_SCORE, bands=[
                {"code": "LOW", "upTo": float("nan")}, {"code": "HIGH"}
            ])])

    def test_the_json_literals_are_rejected_too(self):
        payload = payload_with_scoring([SIMPLE_SCORE])
        text = json.dumps(payload).replace('"weight": 2', '"weight": Infinity')
        self.assertIn("Infinity", text)
        with self.assertRaises(DefinitionError):
            SurveyDefinition.from_json(text)


if __name__ == "__main__":
    unittest.main()
