"""考试答案键测试的共用零件（WP-09.1，契约 survey-exam-v1）。

高熵哨兵：端到端要在整页 HTML 与全部 JS 里搜它。用普通词（"PARIS"）搜不出问题，
因为页面里本来就可能有；哨兵保证「搜到了」只有一个解释——答案漏下去了。
"""

import copy

from pubgw.model import SurveyDefinition

from .fixtures import sample_payload

#: 文本题的正确答案。端到端按这个串在页面与 JS 里扫描。
TEXT_SENTINEL = "MJYSENTINELTEXT7Q3K"


def exam_payload(exam, **settings_overrides):
    """带 exam 块的 v1 定义。"""
    payload = copy.deepcopy(sample_payload())
    payload["settings"].update(settings_overrides)
    if exam is not None:
        payload["exam"] = copy.deepcopy(exam)
    return payload


def exam_definition(exam, **settings_overrides):
    return SurveyDefinition.from_dict(exam_payload(exam, **settings_overrides))


def full_exam():
    """三种客观题各一道：单选、多选、短文本。"""
    return {
        "examVersion": 1,
        "answerKey": [
            {"question": "QSINGLE", "correct": ["A2"], "points": 5},
            {"question": "QMULTI", "correct": ["SQ001", "SQ002"], "points": 4},
            {"question": "QTEXT", "correct": [TEXT_SENTINEL], "points": 3, "ignoreCase": True},
        ],
    }


def single_key(**overrides):
    """只有一道单选题的答案键，用来逐字段试错。"""
    entry = {"question": "QSINGLE", "correct": ["A2"], "points": 5}
    entry.update(overrides)
    return {"examVersion": 1, "answerKey": [entry]}
