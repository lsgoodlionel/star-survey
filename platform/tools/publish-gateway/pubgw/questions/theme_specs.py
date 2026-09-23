"""平台自带题型主题的**注册表**，以及只换展示的那几个主题的校验与降级。

骨架在 ``theme_kit.py``，通用列约束在 ``theme_columns.py``；
走副表的题型按批次分两个模块：切片 02.3–02.4 的在 ``theme_structured.py``，
切片 02.5 的在 ``theme_research.py``。本模块只把它们凑成一张表。
"""

from typing import Any, Dict, List

from ..model import Question
from .theme_columns import _check_columns, _lower_table, _parse_columns
from .theme_kit import (
    CELL_MAX_LENGTH, COLUMNS_ATTRIBUTE, HARD_MAX_ROWS, HIGHLIGHT_SEGMENTS_ATTRIBUTE,
    HIGHLIGHT_TEXT_ATTRIBUTE, Issue, LOOP_OBJECTS_ATTRIBUTE, Lowering, MAX_ROWS_ATTRIBUTE,
    MIN_ROWS_ATTRIBUTE, OPTION_REQUIRED, OPTION_VALUE, OptionSpec, PK_ITEMS_ATTRIBUTE,
    PK_PAIRS_ATTRIBUTE, SHELF_IMAGE_ATTRIBUTE, SHELF_PRODUCTS_ATTRIBUTE,
    STRUCTURE_VERSION_ATTRIBUTE, STRUCTURE_VERSION_PATTERN, ThemeSpec, canonical_json,
)
from .theme_research import (
    MAX_HIGHLIGHT_TEXT, check_text_highlight, lower_text_highlight, text_highlight_columns,
)
from .theme_structured import (
    MAX_IMAGE_LENGTH, MAX_SHELF_QUANTITY, check_image_pk, check_loop_rating, check_shelf,
    heatmap_columns, image_pk_columns, loop_rating_columns, lower_heatmap, lower_image_pk,
    lower_loop_rating, lower_shelf, shelf_columns,
)


def _structure_version() -> OptionSpec:
    """副表题型都要声明结构版本：改了列定义不换版本，早先的答卷就读不回来了。"""
    return OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                      max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                      pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）")


# ------------------------------------------------------------------ 只换展示的那几个


def _grouped_option_codes(question: Question) -> List[str]:
    """本题「一个选项行一个代码」的那一批代码。

    单选（``L``）的选项是答案选项，多选（``M``）的选项是子题——两边的代码都在答卷里
    对应一列（多选是子题列，单选是同一列的取值），但来源不是同一张表，不能混用。
    「其他」项没有代码，不参与分组。
    """
    if question.type == "M":
        return [sub.code for sub in question.subquestions]
    return [answer.code for answer in question.answers_on_scale(0)]


def _check_groups(question: Question, values: Dict[str, Any]) -> List[Issue]:
    """分组必须恰好覆盖本题的全部选项：漏掉一个就是拼错了，别让它在页面上消失。"""
    codes = _grouped_option_codes(question)
    issues: List[Issue] = []
    listed: List[str] = []
    for index, raw in enumerate(values.get("groups") or []):
        path = "themeOptions.groups[{}]".format(index)
        if not isinstance(raw, dict) or not isinstance(raw.get("label"), str) or not raw["label"].strip():
            issues.append((OPTION_VALUE, path + ".label", "每一组都要有非空的 label"))
            continue
        members = raw.get("codes")
        if not isinstance(members, list) or not members or any(not isinstance(item, str) for item in members):
            issues.append((OPTION_VALUE, path + ".codes", "每一组的 codes 必须是非空字符串数组"))
            continue
        listed.extend(members)
    if issues:
        return issues
    unknown = sorted(set(listed) - set(codes))
    if unknown:
        issues.append((OPTION_VALUE, "themeOptions.groups", "分组里的 {} 不是本题的选项代码".format("、".join(unknown))))
    duplicated = sorted({code for code in listed if listed.count(code) > 1})
    if duplicated:
        issues.append((OPTION_VALUE, "themeOptions.groups", "选项 {} 被分进了多个组".format("、".join(duplicated))))
    missing = [code for code in codes if code not in listed]
    if missing:
        issues.append((OPTION_VALUE, "themeOptions.groups", "选项 {} 没有分组".format("、".join(missing))))
    return issues


def _lower_groups(question: Question, values: Dict[str, Any]) -> Lowering:
    groups = [{"label": group["label"], "codes": list(group["codes"])} for group in values["groups"]]
    return Lowering(attributes={"mjy_option_groups": canonical_json(groups)})


def _check_scan(question: Question, values: Dict[str, Any]) -> List[Issue]:
    """扫码得到的字符串完全由客户端决定，没有长度闸门就等于没有闸门。"""
    if question.max_length is None:
        return [(OPTION_REQUIRED, "maxLength", "扫码题必须设 maxLength：扫到的内容整串由客户端提交")]
    return []


def _lower_inline_blank(question: Question, values: Dict[str, Any]) -> Lowering:
    """清值策略用引擎自带的 commented_checkbox=checked（服务端 _ValidateQuestion 重算），
    长度上限没有原生属性，降成 em_validation_q 的一段。"""
    limit = values["blankMaxLength"]
    columns = ["{}_{}comment.NAOK".format(question.code, sub.code) for sub in question.subquestions]
    if question.other:
        columns.append("{}_othercomment.NAOK".format(question.code))
    rules = tuple(
        "(is_empty({v}) or strlen(html_entity_decode({v})) <= {n})".format(v=column, n=limit)
        for column in columns
    )
    return Lowering(
        attributes={"commented_checkbox": "checked"},
        rules=rules,
        tips=("每处填空最多 {} 个字".format(limit),) if rules else (),
    )


# ------------------------------------------------------------------ 注册表

_THEMES = (
    ThemeSpec(
        name="mjy-collapsible",
        label="折叠栏目",
        requirement="R02-43",
        types=("X",),
        options=(
            OptionSpec("summary", "text", attribute="mjy_collapse_summary", required=True, max_length=120),
            OptionSpec("collapsed", "bool", attribute="mjy_collapse_default", default=True),
        ),
    ),
    ThemeSpec(
        name="mjy-scan-input",
        label="扫码录入",
        requirement="R02-28",
        types=("S",),
        options=(
            OptionSpec("scanFormat", "enum", attribute="mjy_scan_format", default="qr",
                       choices=("qr", "barcode", "any")),
            OptionSpec("manualEntry", "bool", attribute="mjy_scan_manual", default=True),
        ),
        check=_check_scan,
    ),
    ThemeSpec(
        name="mjy-grouped-options",
        label="选项分类",
        requirement="R02-04",
        # 单选按 answers 分组，多选按 subquestions 分组（_grouped_option_codes）。
        # 多选的选项行由 rows/*.twig 包含进来，主题连行模板一起接管，
        # 把子题代码打进行标记，浏览器端才认得出哪一行属于哪一组。
        types=("L", "M"),
        options=(
            OptionSpec("groups", "list", required=True),
            OptionSpec("collapsible", "bool", attribute="mjy_option_groups_collapsible", default=False),
        ),
        check=_check_groups,
        lower=_lower_groups,
    ),
    ThemeSpec(
        name="mjy-matrix-stepper",
        label="矩阵单题作答",
        requirement="R02-14",
        types=("F",),
        options=(
            OptionSpec("rowsPerStep", "integer", attribute="mjy_stepper_rows", default=1, minimum=1, maximum=10),
            OptionSpec("showProgress", "bool", attribute="mjy_stepper_progress", default=True),
        ),
    ),
    ThemeSpec(
        name="mjy-inline-blank",
        label="选项内嵌填空",
        requirement="R02-07",
        types=("P",),
        options=(
            OptionSpec("blankLabel", "text", attribute="mjy_inline_blank_label", default="补充", max_length=40),
            OptionSpec("blankMaxLength", "integer", attribute="mjy_inline_blank_max", required=True,
                       minimum=1, maximum=CELL_MAX_LENGTH),
        ),
        lower=_lower_inline_blank,
    ),
    ThemeSpec(
        name="mjy-repeating-table",
        label="自增表格",
        requirement="R02-13",
        types=("T",),
        options=(
            _structure_version(),
            OptionSpec("columns", "list", required=True),
            OptionSpec("minRows", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0, minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxRows", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=20, minimum=1, maximum=HARD_MAX_ROWS),
        ),
        check=_check_columns,
        lower=_lower_table,
        side_columns=lambda values: _parse_columns(values["columns"]),
    ),
    ThemeSpec(
        name="mjy-heatmap",
        label="热力图选区",
        requirement="R02-19",
        types=("T",),
        options=(
            _structure_version(),
            OptionSpec("image", "text", attribute="mjy_heatmap_image", required=True, max_length=500),
            OptionSpec("minPoints", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0, minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxPoints", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=10, minimum=1, maximum=HARD_MAX_ROWS),
        ),
        check=lambda question, values: (
            [(OPTION_VALUE, "themeOptions.minPoints", "minPoints 不能大于 maxPoints")]
            if values.get("minPoints", 0) > values.get("maxPoints", 0) else []
        ),
        lower=lower_heatmap,
        side_columns=heatmap_columns,
    ),
    ThemeSpec(
        name="mjy-loop-rating",
        label="循环评价",
        requirement="R02-11",
        types=("T",),
        options=(
            _structure_version(),
            # 三份清单都不落成各自的属性：对象进 mjy_loop_objects，
            # 维度与量表已经在生成的列定义里，再存一份等于埋一个会漂的副本。
            OptionSpec("objects", "list", required=True),
            OptionSpec("dimensions", "list", required=True),
            OptionSpec("scale", "list", required=True),
        ),
        check=check_loop_rating,
        lower=lower_loop_rating,
        side_columns=loop_rating_columns,
    ),
    ThemeSpec(
        name="mjy-image-pk",
        label="图片 PK",
        requirement="R02-17",
        types=("T",),
        options=(
            _structure_version(),
            OptionSpec("items", "list", required=True),
            OptionSpec("pairs", "list", required=True),
        ),
        check=check_image_pk,
        lower=lower_image_pk,
        side_columns=image_pk_columns,
    ),
    ThemeSpec(
        name="mjy-shelf",
        label="货架题",
        requirement="R02-18",
        types=("T",),
        options=(
            _structure_version(),
            OptionSpec("image", "text", attribute=SHELF_IMAGE_ATTRIBUTE, required=True,
                       max_length=MAX_IMAGE_LENGTH),
            OptionSpec("products", "list", required=True),
            OptionSpec("minPicks", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0,
                       minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxPicks", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=10,
                       minimum=1, maximum=HARD_MAX_ROWS),
            OptionSpec("maxQuantity", "integer", default=99, minimum=1, maximum=MAX_SHELF_QUANTITY),
        ),
        check=check_shelf,
        lower=lower_shelf,
        side_columns=shelf_columns,
    ),
    ThemeSpec(
        name="mjy-text-highlight",
        label="文字点睛",
        requirement="R02-22",
        types=("T",),
        options=(
            _structure_version(),
            # 原文与片段都不单独落成「作者可直写」的属性：它们由降级统一生成，
            # 片段代码里带着偏移与原文指纹（theme_research._segment_code）。
            OptionSpec("text", "text", required=True, max_length=MAX_HIGHLIGHT_TEXT),
            OptionSpec("segments", "list", required=True),
            OptionSpec("tags", "list", required=True),
            OptionSpec("minMarks", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0,
                       minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxMarks", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=10,
                       minimum=1, maximum=HARD_MAX_ROWS),
        ),
        check=check_text_highlight,
        lower=lower_text_highlight,
        side_columns=text_highlight_columns,
    ),
)

THEMES: Dict[str, ThemeSpec] = {theme.name: theme for theme in _THEMES}

#: 作答走 JSON 信封＋副表的主题（插件按它认题，见 MjyThemedQuestionMap）。
STRUCTURED_THEMES = tuple(theme.name for theme in _THEMES if theme.has_side_table)

#: 结构版本必须是这个形状才会被插件采纳，否则回落到「不知道是哪一版」。
STRUCTURE_VERSION_ATTRIBUTE_NAME = STRUCTURE_VERSION_ATTRIBUTE

#: 校验时拒绝作者直写的属性：它们由 themeOptions 生成，两边都写等于埋一个冲突。
MANAGED_ATTRIBUTES = frozenset(
    {COLUMNS_ATTRIBUTE, MIN_ROWS_ATTRIBUTE, MAX_ROWS_ATTRIBUTE, STRUCTURE_VERSION_ATTRIBUTE,
     LOOP_OBJECTS_ATTRIBUTE, PK_ITEMS_ATTRIBUTE, PK_PAIRS_ATTRIBUTE,
     SHELF_IMAGE_ATTRIBUTE, SHELF_PRODUCTS_ATTRIBUTE,
     HIGHLIGHT_TEXT_ATTRIBUTE, HIGHLIGHT_SEGMENTS_ATTRIBUTE,
     "mjy_option_groups", "commented_checkbox"}
)
