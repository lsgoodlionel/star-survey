"""题型形状表：一道题在 get_fieldmap 里应该出现哪些行。

引擎的列结构由 createFieldMap() 按题型硬编码生成
（application/helpers/common_helper.php:1704 起），没有任何数据可以查询。
原型只支持下表里的题型，遇到表外的题型宁可拒绝发布，也不要在校验阶段
「看起来通过了」而实际上没人检查过它的列。
"""

from dataclasses import dataclass
from typing import Optional, Tuple

from .model import Question

#: aid 后缀：多选带评论题给每个子题额外生成一列。
COMMENT_SUFFIX = "comment"
OTHER_AID = "other"
OTHER_COMMENT_AID = "othercomment"


@dataclass(frozen=True)
class QuestionShape:
    """某个题型的结构约束与列形状。"""

    type: str
    label: str
    needs_answers: bool = False
    needs_subquestions: bool = False
    allows_other: bool = False
    has_comment_column: bool = False
    answer_scales: Tuple[int, ...] = (0,)


_SHAPES = (
    QuestionShape("L", "单选（单选按钮）", needs_answers=True, allows_other=True),
    QuestionShape("!", "单选（下拉）", needs_answers=True, allows_other=True),
    QuestionShape("M", "多选", needs_subquestions=True, allows_other=True),
    QuestionShape(
        "P", "多选带评论", needs_subquestions=True, allows_other=True, has_comment_column=True
    ),
    QuestionShape("F", "数组（每行一组选项）", needs_answers=True, needs_subquestions=True),
    QuestionShape(
        "1", "数组（双尺度）", needs_answers=True, needs_subquestions=True, answer_scales=(0, 1)
    ),
    QuestionShape("S", "短文本"),
    QuestionShape("T", "长文本"),
    QuestionShape("U", "超长文本"),
    QuestionShape("N", "数值"),
    QuestionShape("D", "日期"),
    # 说明文字题不收集任何作答，但引擎照样给它一列：createFieldMap() 的
    # 「无子题」分支把 X 和普通文本题一视同仁（common_helper.php:1759），
    # 所以 get_fieldmap 里有它，答卷表里也有它。实测确认，不是推测。
    QuestionShape("X", "说明文字"),
)

SHAPES = {shape.type: shape for shape in _SHAPES}

#: 供错误信息与文档使用的题型清单。
SUPPORTED_TYPES = tuple(shape.type for shape in _SHAPES)


def shape_of(question_type: str) -> Optional[QuestionShape]:
    """未知题型返回 None，由调用方决定怎么报错。"""
    return SHAPES.get(question_type)


def expected_rows(question: Question) -> Tuple[Tuple[str, str, int], ...]:
    """这道题应该在 get_fieldmap 里产生的 (题目代码, aid, 尺度) 列表。

    未知题型返回空元组：校验阶段已经把它挡下了，这里不再二次报错。
    """
    shape = shape_of(question.type)
    if shape is None:
        return ()

    rows = []
    if shape.needs_subquestions:
        for subquestion in question.subquestions:
            for scale in shape.answer_scales:
                rows.append((question.code, subquestion.code, scale))
            if shape.has_comment_column:
                rows.append((question.code, subquestion.code + COMMENT_SUFFIX, 0))
    else:
        rows.append((question.code, "", 0))

    if question.other and shape.allows_other:
        rows.append((question.code, OTHER_AID, 0))
        if shape.has_comment_column:
            rows.append((question.code, OTHER_COMMENT_AID, 0))
    return tuple(rows)
