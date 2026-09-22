"""题型形状表：一道题在 get_fieldmap 里应该出现哪些行。

引擎的列结构由 createFieldMap() 按题型硬编码生成
（application/helpers/common_helper.php:1704 起），没有任何数据可以查询。
只支持下表里的题型，遇到表外的题型宁可拒绝发布，也不要在校验阶段
「看起来通过了」而实际上没人检查过它的列。WP-02 的题型映射见
platform/docs/p2/question-type-map.md。
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .model import Question

#: aid 后缀：多选带评论题给每个子题额外生成一列。
COMMENT_SUFFIX = "comment"
OTHER_AID = "other"
OTHER_COMMENT_AID = "othercomment"
#: 单选带评论（O）的评论列、上传题（|）的文件计数列。
COMMENT_AID = "comment"
FILECOUNT_AID = "filecount"
#: 排序题限制名次数的属性（createFieldMap() 按它截断名次列）。
RANKING_LIMIT_ATTRIBUTE = "max_subquestions"

#: 列的排布方式（对应 createFieldMap() 的分支）。
SINGLE = "single"  # 一列（无子题）
PER_SUBQUESTION = "per_subquestion"  # 每个子题一列（多选、数组、多项填空…）
MATRIX = "matrix"  # 行子题（尺度 0）× 列子题（尺度 1），aid = 行_列
RANKING = "ranking"  # 一列 JSON ＋ 每个名次一条虚列（aid = 1…n，无物理列）


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
    layout: str = ""
    #: 子题可用的尺度：矩阵题的行在尺度 0、列在尺度 1。
    subquestion_scales: Tuple[int, ...] = (0,)
    #: 主列之后固定追加的列（O 的 comment、| 的 filecount）。
    extra_aids: Tuple[str, ...] = ()
    #: 引擎内置、不走 answers 的取值（checkValidityAnswer 按它拒收）。
    fixed_options: Tuple[str, ...] = ()
    default_theme: str = ""

    @property
    def column_layout(self) -> str:
        if self.layout:
            return self.layout
        return PER_SUBQUESTION if self.needs_subquestions else SINGLE


_FIVE = ("1", "2", "3", "4", "5")
_TEN = _FIVE + ("6", "7", "8", "9", "10")

_SHAPES = (
    QuestionShape("L", "单选（单选按钮）", needs_answers=True, allows_other=True, default_theme="listradio"),
    QuestionShape("!", "单选（下拉）", needs_answers=True, allows_other=True, default_theme="list_dropdown"),
    QuestionShape("M", "多选", needs_subquestions=True, allows_other=True, fixed_options=("Y",),
                  default_theme="multiplechoice"),
    QuestionShape(
        "P", "多选带评论", needs_subquestions=True, allows_other=True, has_comment_column=True,
        fixed_options=("Y",), default_theme="multiplechoice_with_comments",
    ),
    QuestionShape("F", "数组（每行一组选项）", needs_answers=True, needs_subquestions=True,
                  default_theme="arrays/array"),
    QuestionShape(
        "1", "数组（双尺度）", needs_answers=True, needs_subquestions=True, answer_scales=(0, 1),
        default_theme="arrays/dualscale",
    ),
    QuestionShape("S", "短文本", default_theme="shortfreetext"),
    QuestionShape("T", "长文本", default_theme="longfreetext"),
    QuestionShape("U", "超长文本", default_theme="hugefreetext"),
    QuestionShape("N", "数值", default_theme="numerical"),
    QuestionShape("D", "日期", default_theme="date"),
    # 说明文字题不收集任何作答，但引擎照样给它一列：createFieldMap() 的
    # 「无子题」分支把 X 和普通文本题一视同仁（common_helper.php:1759），
    # 所以 get_fieldmap 里有它，答卷表里也有它。实测确认，不是推测。
    QuestionShape("X", "说明文字", default_theme="boilerplate"),
    # 计算值（引擎的 Equation 题）：同样走「无子题」分支，答卷里一列，
    # 值由引擎按 equation 属性算出后写入。只能由 v2 定义的 calculation 产生。
    QuestionShape("*", "计算值", default_theme="equation"),
    # ---- WP-02 切片 02.1–02.2：其余原生题型（common_helper.php:1761 起的各分支）。
    QuestionShape("O", "单选带评论", needs_answers=True, extra_aids=(COMMENT_AID,),
                  default_theme="list_with_comment"),
    QuestionShape("5", "五分制", fixed_options=_FIVE, default_theme="5pointchoice"),
    QuestionShape("Y", "是／否", fixed_options=("Y", "N"), default_theme="yesno"),
    QuestionShape("G", "性别", fixed_options=("F", "M"), default_theme="gender"),
    QuestionShape("H", "数组（按列）", needs_answers=True, needs_subquestions=True,
                  default_theme="arrays/column"),
    QuestionShape("A", "数组（五分制）", needs_subquestions=True, fixed_options=_FIVE,
                  default_theme="arrays/5point"),
    QuestionShape("B", "数组（十分制）", needs_subquestions=True, fixed_options=_TEN,
                  default_theme="arrays/10point"),
    QuestionShape("C", "数组（是／不确定／否）", needs_subquestions=True, fixed_options=("Y", "U", "N"),
                  default_theme="arrays/yesnouncertain"),
    QuestionShape("E", "数组（增加／不变／减少）", needs_subquestions=True, fixed_options=("I", "S", "D"),
                  default_theme="arrays/increasesamedecrease"),
    QuestionShape("K", "多项数值", needs_subquestions=True, default_theme="multiplenumeric"),
    QuestionShape("Q", "多项填空", needs_subquestions=True, default_theme="multipleshorttext"),
    QuestionShape(":", "数组（数值）", needs_subquestions=True, layout=MATRIX, subquestion_scales=(0, 1),
                  default_theme="arrays/multiflexi"),
    QuestionShape(";", "数组（文本）", needs_subquestions=True, layout=MATRIX, subquestion_scales=(0, 1),
                  default_theme="arrays/texts"),
    # 7.x 起排序项是子题，答卷只有一列 JSON（SurveyActivator.php:245），
    # 名次列只存在于 fieldmap（common_helper.php:1984），平台照实记账。
    QuestionShape("R", "排序", needs_subquestions=True, layout=RANKING, default_theme="ranking"),
    QuestionShape("|", "文件上传", extra_aids=(FILECOUNT_AID,), default_theme="file_upload"),
)

SHAPES = {shape.type: shape for shape in _SHAPES}

#: 供错误信息与文档使用的题型清单。
SUPPORTED_TYPES = tuple(shape.type for shape in _SHAPES)

#: 题型 → 引擎自带的基础题型主题名（题目没指定主题时写进 question_theme_name）。
DEFAULT_THEMES: Dict[str, str] = {shape.type: shape.default_theme for shape in _SHAPES}


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

    layout = shape.column_layout
    if layout == MATRIX:
        return _matrix_rows(question)
    if layout == RANKING:
        return _ranking_rows(question)

    rows = []
    if layout == PER_SUBQUESTION:
        for subquestion in question.subquestions:
            for scale in shape.answer_scales:
                rows.append((question.code, subquestion.code, scale))
            if shape.has_comment_column:
                rows.append((question.code, subquestion.code + COMMENT_SUFFIX, 0))
    else:
        rows.append((question.code, "", 0))
        rows.extend((question.code, aid, 0) for aid in shape.extra_aids)

    if question.other and shape.allows_other:
        rows.append((question.code, OTHER_AID, 0))
        if shape.has_comment_column:
            rows.append((question.code, OTHER_COMMENT_AID, 0))
    return tuple(rows)


def _matrix_rows(question: Question) -> Tuple[Tuple[str, str, int], ...]:
    """行子题 × 列子题，按文档顺序；引擎的 aid 是「行代码_列代码」，尺度恒为 0。"""
    lines = [sub for sub in question.subquestions if sub.scale == 0]
    columns = [sub for sub in question.subquestions if sub.scale == 1]
    return tuple(
        (question.code, "{}_{}".format(line.code, column.code), 0) for line in lines for column in columns
    )


def _ranking_rows(question: Question) -> Tuple[Tuple[str, str, int], ...]:
    """JSON 主列 ＋ 名次 1…n；n 取子题数与 max_subquestions 的较小者（引擎同样截断）。"""
    slots = len(question.subquestions)
    limit = question.attributes.get(RANKING_LIMIT_ATTRIBUTE, "").strip()
    if limit.isdigit() and 0 < int(limit) < slots:
        slots = int(limit)
    rows = [(question.code, "", 0)]
    rows.extend((question.code, str(rank), 0) for rank in range(1, slots + 1))
    return tuple(rows)
