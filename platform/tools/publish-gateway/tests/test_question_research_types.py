"""WP-02 切片 02.5 的三类走副表的题型：文字点睛、心理实验、KANO 模型。

三类都没有新增插件校验：列全部落在切片 02.4 引入的通用列约束上
（``type: "enum"`` ＋ ``options``、``distinct``、整数上下限），
所以这里只需要证明「平台生成的列定义是对的」。

副表契约见 platform/contracts/question-extension-tables-v1.md，
四个已有的副表题型在 test_question_side_tables.py。
"""

import json
import unittest

from pubgw.binding import BindingRecord
from pubgw.fieldmap import binding_map, parse_fieldmap
from pubgw.qtypes import expected_rows
from pubgw.questions.themes import structure_digest

from .fixtures import fieldmap_for
from .qtype_fixtures import definition_with, first_question
from .theme_fixtures import (
    HIGHLIGHT_SEGMENTS, HIGHLIGHT_TAGS, HIGHLIGHT_TEXT, KANO_FEATURES, PSYCH_KEYS, PSYCH_TRIALS,
    codes, compile_attributes, kano, psych_trial, text_highlight,
)


def side_table(*questions, code):
    definition = definition_with(*questions)
    rows = parse_fieldmap(fieldmap_for(definition))
    item = {entry.code: entry for entry in binding_map(definition, rows)}[code]
    return item.to_dict()["sideTable"]


# ------------------------------------------------------------------ R02-22 文字点睛


class TextHighlightTest(unittest.TestCase):
    """R02-22 文字点睛：在一段原文上标出若干处，每处给一个标记。

    可标的片段由平台**声明**（一段一个取值），所以「标了原文以外的东西」
    由枚举列自己挡住；同一片段不能标两次由唯一列挡住。两条都是通用列约束，
    插件一行校验代码都不用改。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(text_highlight(**options), code="QMARK")["mjy_table_columns"])

    def test_the_segment_column_is_a_unique_enum_over_the_declared_segments(self):
        segment = self.columns()[0]
        self.assertEqual(("segment", "enum", True, True),
                         (segment["code"], segment["type"], segment["required"], segment["distinct"]))
        self.assertEqual(["苹果很甜", "香蕉太软", "梨子刚好"],
                         [option["label"] for option in segment["options"]])

    def test_the_tag_column_is_an_enum_over_the_declared_tags(self):
        tag = self.columns()[1]
        self.assertEqual(("tag", "enum", True), (tag["code"], tag["type"], tag["required"]))
        self.assertEqual(["like", "dislike"], [option["code"] for option in tag["options"]])
        self.assertNotIn("distinct", tag)

    def test_a_segment_code_carries_its_offset_and_a_fingerprint_of_its_own_text(self):
        """「中文标记偏移和原文版本一致」：取值代码里同时带着偏移与该段原文的指纹。

        于是读端拿到一条标记就知道它指向原文的哪几个字、那几个字当时是什么，
        不必回查当时的原文。
        """
        first = self.columns()[0]["options"][0]["code"]
        self.assertRegex(first, r"\As0_4_[0-9a-f]{6}\Z")

    def test_editing_the_source_text_changes_the_structure_digest(self):
        """编辑原文必须换一版：改了字，那一段的取值代码就变了，结构摘要跟着变。"""
        edited = HIGHLIGHT_TEXT.replace("甜", "酸")
        self.assertNotEqual(structure_digest(self.columns()),
                            structure_digest(self.columns(text=edited)))

    def test_moving_a_segment_changes_the_structure_digest(self):
        moved = [{"start": 5, "length": 4}, {"start": 10, "length": 4}]
        self.assertNotEqual(structure_digest(self.columns()),
                            structure_digest(self.columns(segments=moved)))

    def test_the_mark_count_reaches_the_plugin_row_bounds(self):
        attributes = compile_attributes(text_highlight(minMarks=1, maxMarks=2), code="QMARK")
        self.assertEqual(("1", "2"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_source_text_and_the_segments_reach_the_theme(self):
        attributes = compile_attributes(text_highlight(), code="QMARK")
        self.assertEqual(HIGHLIGHT_TEXT, attributes["mjy_highlight_text"])
        spans = json.loads(attributes["mjy_highlight_segments"])
        self.assertEqual([0, 5, 10], [span["start"] for span in spans])
        self.assertEqual([4, 4, 4], [span["length"] for span in spans])
        self.assertEqual([option["code"] for option in self.columns()[0]["options"]],
                         [span["code"] for span in spans])

    def test_structure_version_text_segments_and_tags_are_required(self):
        for name in ("structureVersion", "text", "segments", "tags"):
            with self.subTest(option=name):
                payload = text_highlight()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_a_segment_must_stay_inside_the_source_text(self):
        for segments in ([{"start": -1, "length": 2}],
                         [{"start": 0, "length": 0}],
                         [{"start": 13, "length": 4}],
                         [{"start": 0}],
                         [{"start": "0", "length": 2}],
                         ["0-2"]):
            with self.subTest(segments=segments):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(text_highlight(segments=segments)))

    def test_segments_may_not_overlap_and_must_be_ordered(self):
        """重叠或乱序的片段让「标了哪几个字」不再唯一，先在发布期挡掉。"""
        for segments in ([{"start": 0, "length": 4}, {"start": 2, "length": 4}],
                         [{"start": 5, "length": 4}, {"start": 0, "length": 4}],
                         [{"start": 0, "length": 4}, {"start": 0, "length": 4}]):
            with self.subTest(segments=segments):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(text_highlight(segments=segments)))

    def test_you_cannot_be_asked_for_more_marks_than_there_are_segments(self):
        # 片段列是唯一列，一个片段最多占一行——要求标的处数多过片段数就永远交不了卷。
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(text_highlight(minMarks=4, maxMarks=4)))

    def test_unordered_mark_bounds_are_rejected(self):
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(text_highlight(minMarks=3, maxMarks=2)))

    def test_bad_tag_lists(self):
        for tags in ([], [{"label": "没有代码"}], [{"code": "like"}, {"code": "like"}],
                     [{"code": "bad code"}]):
            with self.subTest(tags=tags):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(text_highlight(tags=tags)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QMARK", "", 0),), expected_rows(first_question(text_highlight())))

    def test_the_theme_is_only_for_long_free_text(self):
        self.assertEqual(["E_THEME_TYPE_MISMATCH"], codes(dict(text_highlight(), type="S")))

    def test_the_binding_declares_the_segment_and_tag_columns(self):
        declared = side_table(text_highlight(), code="QMARK")
        self.assertEqual(["segment", "tag"], [column["code"] for column in declared["columns"]])
        self.assertEqual("th1", declared["structureVersion"])
        self.assertEqual("question-extension-tables-v1", declared["contract"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_the_binding_record_round_trips_the_side_table(self):
        definition = definition_with(text_highlight())
        item = {entry.code: entry for entry in
                binding_map(definition, parse_fieldmap(fieldmap_for(definition)))}["QMARK"]
        record = BindingRecord("e1", 1, "d", "c", "fm1", "fm1:x", "en", "now", (item,))
        self.assertEqual(item.side_table, BindingRecord.from_dict(record.to_dict()).questions[0].side_table)


# ------------------------------------------------------------------ R02-46 心理实验


class PsychTrialTest(unittest.TestCase):
    """R02-46 心理实验：一行一个试次，记按了哪个键、用了多少毫秒。

    **正确率不是提交上来的**：正确按键随题目定义留痕，正确与否由平台按
    「试次的正确按键 vs 作答的按键」推导。让浏览器端提交 ``correct`` 等于让
    作答者自己宣布答对了。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(psych_trial(**options), code="QPSY")["mjy_table_columns"])

    def test_the_trial_column_is_a_unique_enum_over_the_declared_trials(self):
        trial = self.columns()[0]
        self.assertEqual(("trial", "enum", True, True),
                         (trial["code"], trial["type"], trial["required"], trial["distinct"]))
        self.assertEqual(["T1", "T2"], [option["code"] for option in trial["options"]])

    def test_the_key_column_is_an_enum_over_the_declared_keys(self):
        key = self.columns()[1]
        self.assertEqual(("key", "enum", True), (key["code"], key["type"], key["required"]))
        self.assertEqual(["left", "right"], [option["code"] for option in key["options"]])

    def test_the_reaction_time_is_a_bounded_integer_in_milliseconds(self):
        reaction = self.columns()[2]
        self.assertEqual(("rt", "integer", True, 0, 5000),
                         (reaction["code"], reaction["type"], reaction["required"],
                          reaction["min"], reaction["max"]))

    def test_there_is_no_column_the_respondent_could_claim_correctness_in(self):
        """正确率只能由平台推导，不能由作答者宣布。"""
        self.assertEqual(["trial", "key", "rt"], [column["code"] for column in self.columns()])

    def test_the_row_count_is_pinned_to_the_number_of_trials(self):
        attributes = compile_attributes(psych_trial(), code="QPSY")
        self.assertEqual(("2", "2"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_trials_reach_the_theme_with_their_stimulus_and_correct_key(self):
        value = compile_attributes(psych_trial(), code="QPSY")["mjy_psych_trials"]
        self.assertEqual(PSYCH_TRIALS, json.loads(value))
        self.assertNotIn(" ", value)

    def test_a_trial_without_a_correct_key_is_allowed(self):
        """练习试次与无正确答案的试次（如偏好判断）没有「对错」可言。"""
        trials = [{"code": "T1", "label": "练习", "stimulus": "红"}]
        value = compile_attributes(psych_trial(trials=trials), code="QPSY")["mjy_psych_trials"]
        self.assertEqual([{"code": "T1", "label": "练习", "stimulus": "红"}], json.loads(value))

    def test_structure_version_trials_and_keys_are_required(self):
        for name in ("structureVersion", "trials", "keys"):
            with self.subTest(option=name):
                payload = psych_trial()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_every_trial_needs_a_stimulus(self):
        for trials in ([{"code": "T1", "label": "第一试次"}],
                       [{"code": "T1", "label": "第一试次", "stimulus": ""}],
                       [{"code": "T1", "label": "第一试次", "stimulus": 7}]):
            with self.subTest(trials=trials):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(psych_trial(trials=trials)))

    def test_a_correct_key_must_be_one_of_the_declared_keys(self):
        trials = [dict(PSYCH_TRIALS[0], correct="middle")]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(psych_trial(trials=trials)))

    def test_bad_trial_and_key_lists(self):
        for options in ({"trials": [{"label": "没有代码", "stimulus": "红"}]},
                        {"trials": [dict(PSYCH_TRIALS[0]), dict(PSYCH_TRIALS[0])]},
                        {"keys": [{"code": "bad key"}]},
                        {"keys": [{"code": "left"}, {"code": "left"}]}):
            with self.subTest(options=options):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(psych_trial(**options)))

    def test_too_many_trials(self):
        # 试次数就是行数，上限与插件的行数硬上限一致。
        many = [{"code": "T{}".format(index), "stimulus": "红"} for index in range(501)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(psych_trial(trials=many)))

    def test_the_reaction_time_cap_is_bounded(self):
        for cap in (0, 600001):
            with self.subTest(cap=cap):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(psych_trial(maxReactionMs=cap)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QPSY", "", 0),), expected_rows(first_question(psych_trial())))

    def test_the_theme_is_only_for_long_free_text(self):
        self.assertEqual(["E_THEME_TYPE_MISMATCH"], codes(dict(psych_trial(), type="S")))

    def test_the_binding_declares_the_trial_key_and_reaction_time_columns(self):
        declared = side_table(psych_trial(), code="QPSY")
        self.assertEqual(["trial", "key", "rt"], [column["code"] for column in declared["columns"]])
        self.assertEqual("ps1", declared["structureVersion"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_changing_the_reaction_time_cap_changes_the_structure_digest(self):
        self.assertNotEqual(structure_digest(self.columns()),
                            structure_digest(self.columns(maxReactionMs=9000)))


# ------------------------------------------------------------------ R02-47 专业模型（KANO）


class KanoModelTest(unittest.TestCase):
    """R02-47 专业模型：按模型设计生成采集结构，而不是只给一个题目标题。

    KANO 每个功能点问两遍——「具备时你怎么想」「不具备时你怎么想」，两问共用同一套
    **由模型固定的**五点量表。量表不是 ``themeOptions`` 的一项：改了它算出来的就不是
    KANO 分类，这正是「按模型生成结构」与「作者自己搭一个矩阵」的区别。
    """

    def columns(self, **options):
        return json.loads(compile_attributes(kano(**options), code="QKANO")["mjy_table_columns"])

    def test_the_feature_column_is_a_unique_enum_over_the_declared_features(self):
        feature = self.columns()[0]
        self.assertEqual(("feature", "enum", True, True),
                         (feature["code"], feature["type"], feature["required"], feature["distinct"]))
        self.assertEqual(["F1", "F2"], [option["code"] for option in feature["options"]])

    def test_both_questions_share_the_scale_the_model_fixes(self):
        functional, dysfunctional = self.columns()[1], self.columns()[2]
        self.assertEqual(["functional", "dysfunctional"], [functional["code"], dysfunctional["code"]])
        for column in (functional, dysfunctional):
            self.assertEqual(("enum", True), (column["type"], column["required"]))
            self.assertEqual(["like", "must", "neutral", "live", "dislike"],
                             [option["code"] for option in column["options"]])
            self.assertEqual(["喜欢这样", "理所当然", "无所谓", "勉强接受", "不喜欢这样"],
                             [option["label"] for option in column["options"]])

    def test_the_scale_is_not_an_author_supplied_option(self):
        """作者改不了量表：改了就不是 KANO 了，分类表也不再适用。"""
        self.assertEqual(["E_THEME_OPTION_UNKNOWN"],
                         codes(kano(scale=[{"code": "1", "label": "好"}])))

    def test_the_row_count_is_pinned_to_the_number_of_features(self):
        attributes = compile_attributes(kano(), code="QKANO")
        self.assertEqual(("2", "2"), (attributes["mjy_table_min_rows"], attributes["mjy_table_max_rows"]))

    def test_the_features_reach_the_theme(self):
        value = compile_attributes(kano(), code="QKANO")["mjy_model_features"]
        self.assertEqual(KANO_FEATURES, json.loads(value))
        self.assertNotIn(" ", value)

    def test_the_model_name_is_recorded_for_the_read_side(self):
        """读端靠它知道「这道题该用哪张分类表」，不必去猜列名。"""
        self.assertEqual("kano", compile_attributes(kano(), code="QKANO")["mjy_model_name"])

    def test_structure_version_and_features_are_required(self):
        for name in ("structureVersion", "features"):
            with self.subTest(option=name):
                payload = kano()
                del payload["themeOptions"][name]
                self.assertEqual(["E_THEME_OPTION_REQUIRED"], codes(payload))

    def test_bad_feature_lists(self):
        for features in ([{"label": "没有代码"}],
                         [{"code": "F1"}, {"code": "F1"}],
                         [{"code": "bad code"}],
                         [{"code": "F1", "label": 7}]):
            with self.subTest(features=features):
                self.assertEqual(["E_THEME_OPTION_VALUE"], codes(kano(features=features)))

    def test_too_many_features(self):
        many = [{"code": "F{}".format(index), "label": str(index)} for index in range(501)]
        self.assertEqual(["E_THEME_OPTION_VALUE"], codes(kano(features=many)))

    def test_the_answer_is_still_one_engine_column(self):
        self.assertEqual((("QKANO", "", 0),), expected_rows(first_question(kano())))

    def test_the_theme_is_only_for_long_free_text(self):
        self.assertEqual(["E_THEME_TYPE_MISMATCH"], codes(dict(kano(), type="S")))

    def test_the_binding_declares_the_feature_and_the_two_question_columns(self):
        declared = side_table(kano(), code="QKANO")
        self.assertEqual(["feature", "functional", "dysfunctional"],
                         [column["code"] for column in declared["columns"]])
        self.assertEqual("kn1", declared["structureVersion"])
        self.assertTrue(declared["structureDigest"].startswith("sd1:"))

    def test_adding_a_feature_changes_the_structure_digest(self):
        wider = KANO_FEATURES + [{"code": "F3", "label": "多端同步"}]
        self.assertNotEqual(structure_digest(self.columns()),
                            structure_digest(self.columns(features=wider)))


class KanoClassificationTableTest(unittest.TestCase):
    """分类表是**确定的**：给定 (functional, dysfunctional) 就有唯一一个 KANO 类别。

    本切片不在网关里算分类（那是 WP-08 的统计口径），但要证明生成出来的两列
    恰好张成分类表的 5×5 定义域——少一个取值，分类表就有格子填不进去。
    """

    def test_the_two_columns_span_the_whole_five_by_five_table(self):
        columns = {column["code"]: column
                   for column in json.loads(compile_attributes(kano(), code="QKANO")["mjy_table_columns"])}
        functional = [option["code"] for option in columns["functional"]["options"]]
        dysfunctional = [option["code"] for option in columns["dysfunctional"]["options"]]
        self.assertEqual(functional, dysfunctional)
        self.assertEqual(25, len(functional) * len(dysfunctional))


class PsychTrialFixtureTest(unittest.TestCase):
    """样例本身的自洽：每个试次的正确按键都在声明过的按键里。"""

    def test_every_sample_correct_key_is_a_declared_key(self):
        declared = {key["code"] for key in PSYCH_KEYS}
        for trial in PSYCH_TRIALS:
            self.assertIn(trial["correct"], declared)


class TextHighlightSegmentsFixtureTest(unittest.TestCase):
    """样例本身的自洽：片段真的落在原文里（写错样例会让上面的断言假装通过）。"""

    def test_every_sample_segment_is_inside_the_sample_text(self):
        for span in HIGHLIGHT_SEGMENTS:
            self.assertLessEqual(span["start"] + span["length"], len(HIGHLIGHT_TEXT))

    def test_the_sample_tags_are_distinct(self):
        self.assertEqual(len(HIGHLIGHT_TAGS), len({tag["code"] for tag in HIGHLIGHT_TAGS}))
