"""平台自带的各个题型主题，一个需求一段。

骨架在 ``theme_kit.py``，列字典在 ``theme_columns.py``；本模块只声明
「这个主题有哪些选项、怎么校验、降成什么属性、副表的列长什么样」。
"""

from typing import Any, Dict, List, Tuple

from ..model import Question
from .theme_columns import _check_columns, _code_list, _lower_table, _parse_columns
from .theme_kit import (
    CELL_MAX_LENGTH, COLUMNS_ATTRIBUTE, HARD_MAX_ROWS, Issue, LOOP_OBJECTS_ATTRIBUTE, Lowering,
    MAX_COLUMNS, MAX_COLUMN_OPTIONS, MIN_ROWS_ATTRIBUTE,
    MAX_ROWS_ATTRIBUTE, OPTION_REQUIRED, OPTION_VALUE, OptionSpec, PK_ITEMS_ATTRIBUTE,
    PK_PAIRS_ATTRIBUTE, SHELF_IMAGE_ATTRIBUTE, SHELF_PRODUCTS_ATTRIBUTE,
    STRUCTURE_VERSION_ATTRIBUTE, STRUCTURE_VERSION_PATTERN, ThemeSpec, _COLUMN_CODE_PATTERN,
    canonical_json,
)

# ---------------------------------------------------------------- 循环评价（R02-11）

#: 评价对象那一列的代码。维度不许叫这个名字，否则对象列会被顶掉。
LOOP_OBJECT_COLUMN = "target"


def _loop_lists(values: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]],
                                                 List[Dict[str, str]], List[Issue]]:
    """解析三份清单，顺带把三处 422 合在一起报出去。"""
    objects, issues = _code_list(values.get("objects"), "themeOptions.objects", HARD_MAX_ROWS)
    # 维度直接当列代码用，所以按列代码的字符集判（不能数字打头）。
    dimensions, found = _code_list(values.get("dimensions"), "themeOptions.dimensions",
                                   MAX_COLUMNS - 1, _COLUMN_CODE_PATTERN)
    issues.extend(found)
    scale, found = _code_list(values.get("scale"), "themeOptions.scale", MAX_COLUMN_OPTIONS)
    issues.extend(found)
    return objects, dimensions, scale, issues


def _check_loop_rating(question: Question, values: Dict[str, Any]) -> List[Issue]:
    objects, dimensions, _scale, issues = _loop_lists(values)
    if issues:
        return issues
    collision = [item["code"] for item in dimensions if item["code"] == LOOP_OBJECT_COLUMN]
    if collision:
        issues.append((OPTION_VALUE, "themeOptions.dimensions",
                       "维度代码不能叫 {}：那是评价对象列".format(LOOP_OBJECT_COLUMN)))
    if not objects:
        issues.append((OPTION_VALUE, "themeOptions.objects", "至少要有一个评价对象"))
    return issues


def _loop_rating_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一行一个评价对象：第一列认对象（枚举＋唯一），其余每个维度一列（枚举到量表）。

    行数在 ``_lower_loop_rating`` 里被钉死成对象个数，「枚举＋唯一＋行数」三者
    合起来就逼出「每个对象恰好评一次」，不必再写一套按行下标的规则。
    """
    objects, dimensions, scale, _issues = _loop_lists(values)
    columns = [{"code": LOOP_OBJECT_COLUMN, "label": "评价对象", "type": "enum",
                "required": True, "options": objects, "distinct": True}]
    columns.extend({"code": item["code"], "label": item["label"], "type": "enum",
                    "required": True, "options": scale} for item in dimensions)
    return columns


def _lower_loop_rating(question: Question, values: Dict[str, Any]) -> Lowering:
    objects, _dimensions, _scale, _issues = _loop_lists(values)
    count = str(len(objects))
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(_loop_rating_columns(values)),
        LOOP_OBJECTS_ATTRIBUTE: canonical_json(objects),
        # 每个对象各一行，不多不少：行数不是作答者能改的东西。
        MIN_ROWS_ATTRIBUTE: count,
        MAX_ROWS_ATTRIBUTE: count,
    })


# ---------------------------------------------------------------- 图片 PK（R02-17）

#: 记录「这一对里哪张图先展示」的列名后缀。
PK_SHOWN_SUFFIX = "_shown"
#: 一对占两列（选了谁＋谁先展示），所以对数上限是列数上限的一半。
MAX_PK_PAIRS = MAX_COLUMNS // 2
#: 留出 ``_shown`` 后缀的余地，配对代码比普通列代码短一截。
MAX_PK_PAIR_CODE = 32 - len(PK_SHOWN_SUFFIX)
MAX_IMAGE_LENGTH = 500


def _pk_items(values: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Issue]]:
    """参赛图片：代码＋标签＋图片地址。图片地址必填——没有图就没有 PK。"""
    items, issues = _code_list(values.get("items"), "themeOptions.items", MAX_COLUMN_OPTIONS)
    if issues:
        return [], issues
    declared = values.get("items") or []
    for index, (item, raw) in enumerate(zip(items, declared)):
        image = raw.get("image") if isinstance(raw, dict) else None
        if not isinstance(image, str) or not image.strip() or len(image) > MAX_IMAGE_LENGTH:
            issues.append((OPTION_VALUE, "themeOptions.items[{}].image".format(index),
                           "每张图都要有图片地址，最长 {} 个字符".format(MAX_IMAGE_LENGTH)))
            continue
        item["image"] = image
    return ([], issues) if issues else (items, [])


def _pk_pairs(values: Dict[str, Any], item_codes: List[str]) -> Tuple[List[Dict[str, str]], List[Issue]]:
    """配对：两张**不同的**、都已声明的图。配对本身由平台声明，随结构版本一起留痕。"""
    raw_pairs = values.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        return [], [(OPTION_VALUE, "themeOptions.pairs", "必须是非空数组")]
    if len(raw_pairs) > MAX_PK_PAIRS:
        return [], [(OPTION_VALUE, "themeOptions.pairs", "最多 {} 对".format(MAX_PK_PAIRS))]
    pairs: List[Dict[str, str]] = []
    issues: List[Issue] = []
    seen = set()
    for index, raw in enumerate(raw_pairs):
        path = "themeOptions.pairs[{}]".format(index)
        code = raw.get("code") if isinstance(raw, dict) else None
        if not isinstance(code, str) or not _COLUMN_CODE_PATTERN.match(code) or len(code) > MAX_PK_PAIR_CODE:
            issues.append((OPTION_VALUE, path + ".code",
                           "配对代码必须以字母开头、只含字母数字与下划线，最长 {} 位".format(MAX_PK_PAIR_CODE)))
            continue
        if code in seen:
            issues.append((OPTION_VALUE, path + ".code", "配对代码重复：{}".format(code)))
            continue
        seen.add(code)
        left, right = raw.get("left"), raw.get("right")
        if left not in item_codes or right not in item_codes or left == right:
            issues.append((OPTION_VALUE, path, "一对必须是两张不同的、已声明的图"))
            continue
        pairs.append({"code": code, "left": left, "right": right})
    return ([], issues) if issues else (pairs, [])


def _pk_lists(values: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Issue]]:
    items, issues = _pk_items(values)
    if issues:
        return [], [], issues
    pairs, found = _pk_pairs(values, [item["code"] for item in items])
    return items, pairs, found


def _check_image_pk(question: Question, values: Dict[str, Any]) -> List[Issue]:
    _items, _pairs, issues = _pk_lists(values)
    return issues


def _image_pk_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """**一对一列**，这一列的可选值恰好是这一对的两张图。

    于是「选了不在这一对里的东西」不需要任何跨列规则，枚举列自己就挡住了；
    另有一列记录哪张图先展示——配对固定、展示顺序随机，随机的那部分要留痕
    才谈得上可追溯（R02-17）。
    """
    items, pairs, _issues = _pk_lists(values)
    labels = {item["code"]: item["label"] for item in items}
    columns: List[Dict[str, Any]] = []
    for pair in pairs:
        options = [{"code": pair[side], "label": labels[pair[side]]} for side in ("left", "right")]
        title = "{} / {}".format(options[0]["label"], options[1]["label"])
        columns.append({"code": pair["code"], "label": title, "type": "enum",
                        "required": True, "options": options})
        # 关掉 JavaScript 直接填信封的那条路径给不出展示顺序，所以这一列不必填。
        columns.append({"code": pair["code"] + PK_SHOWN_SUFFIX, "label": title + "（先展示）",
                        "type": "enum", "required": False, "options": options})
    return columns


def _lower_image_pk(question: Question, values: Dict[str, Any]) -> Lowering:
    items, pairs, _issues = _pk_lists(values)
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(_image_pk_columns(values)),
        PK_ITEMS_ATTRIBUTE: canonical_json(items),
        PK_PAIRS_ATTRIBUTE: canonical_json(pairs),
        # 整题就是一行：每一对是这一行里的一列。
        MIN_ROWS_ATTRIBUTE: "1",
        MAX_ROWS_ATTRIBUTE: "1",
    })


# ---------------------------------------------------------------- 货架题（R02-18）

#: 商品数量的绝对上限：一次作答说「我拿了一万件」没有意义，先把最坏情况框住。
MAX_SHELF_QUANTITY = 999
#: 热区的四个归一化坐标（左上角 ＋ 宽高），一律落在 [0,1] 内。
_HOTSPOT_KEYS = ("x", "y", "w", "h")


def _hotspot_issues(raw: Any, index: int) -> List[Issue]:
    """热区坐标：归一化到 [0,1]，换了货架图的尺寸也不用改坐标。"""
    path = "themeOptions.products[{}]".format(index)
    box: Dict[str, float] = {}
    for key in _HOTSPOT_KEYS:
        value = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [(OPTION_VALUE, "{}.{}".format(path, key), "{} 必须是数字".format(key))]
        box[key] = float(value)
    if not (0 <= box["x"] <= 1 and 0 <= box["y"] <= 1):
        return [(OPTION_VALUE, path, "热区的左上角必须落在 [0,1] 内")]
    if not (0 < box["w"] <= 1 and 0 < box["h"] <= 1):
        return [(OPTION_VALUE, path, "热区的宽高必须在 (0,1] 内")]
    if box["x"] + box["w"] > 1 or box["y"] + box["h"] > 1:
        return [(OPTION_VALUE, path, "热区不能越出货架图")]
    return []


def _shelf_products(values: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Issue]]:
    """商品：代码＋标签＋货架图上的热区。"""
    products, issues = _code_list(values.get("products"), "themeOptions.products", MAX_COLUMN_OPTIONS)
    if issues:
        return [], issues
    declared = values.get("products") or []
    for index, (product, raw) in enumerate(zip(products, declared)):
        found = _hotspot_issues(raw, index)
        issues.extend(found)
        if not found:
            product.update({key: raw[key] for key in _HOTSPOT_KEYS})
    return ([], issues) if issues else (products, [])


def _check_shelf(question: Question, values: Dict[str, Any]) -> List[Issue]:
    products, issues = _shelf_products(values)
    if issues:
        return issues
    least, most = values.get("minPicks", 0), values.get("maxPicks", 0)
    if least > most:
        return [(OPTION_VALUE, "themeOptions.minPicks", "minPicks 不能大于 maxPicks")]
    # 商品列是唯一列，一件商品最多占一行——要求取的件数多过货架上的商品就永远交不了卷。
    if least > len(products):
        return [(OPTION_VALUE, "themeOptions.minPicks",
                 "minPicks 超过了货架上的商品数 {}".format(len(products)))]
    return []


def _shelf_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """取了什么（枚举＋唯一）、取了几件（整数带上下限）。

    唯一约束的意思是「同一件商品不能取两次」——要多拿就改数量，
    否则同一件商品会摊成两行，读端得自己求和。
    """
    products, _issues = _shelf_products(values)
    return [
        {"code": "product", "label": "商品", "type": "enum", "required": True, "distinct": True,
         "options": [{"code": item["code"], "label": item["label"]} for item in products]},
        {"code": "qty", "label": "件数", "type": "integer", "required": True,
         "min": 1, "max": values.get("maxQuantity", 99)},
    ]


def _lower_shelf(question: Question, values: Dict[str, Any]) -> Lowering:
    products, _issues = _shelf_products(values)
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(_shelf_columns(values)),
        SHELF_PRODUCTS_ATTRIBUTE: canonical_json(products),
    })


def _heatmap_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """归一化坐标：两列 decimal，范围写死在 0…1，服务端由插件逐格校验。"""
    return [
        {"code": "x", "label": "横向比例", "type": "decimal", "required": True, "min": 0, "max": 1},
        {"code": "y", "label": "纵向比例", "type": "decimal", "required": True, "min": 0, "max": 1},
    ]


def _lower_heatmap(question: Question, values: Dict[str, Any]) -> Lowering:
    return Lowering(attributes={COLUMNS_ATTRIBUTE: canonical_json(_heatmap_columns(values))})


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
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
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
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            OptionSpec("image", "text", attribute="mjy_heatmap_image", required=True, max_length=500),
            OptionSpec("minPoints", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0, minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxPoints", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=10, minimum=1, maximum=HARD_MAX_ROWS),
        ),
        check=lambda question, values: (
            [(OPTION_VALUE, "themeOptions.minPoints", "minPoints 不能大于 maxPoints")]
            if values.get("minPoints", 0) > values.get("maxPoints", 0) else []
        ),
        lower=_lower_heatmap,
        side_columns=_heatmap_columns,
    ),
    ThemeSpec(
        name="mjy-loop-rating",
        label="循环评价",
        requirement="R02-11",
        types=("T",),
        options=(
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            # 三份清单都不落成各自的属性：对象进 mjy_loop_objects，
            # 维度与量表已经在生成的列定义里，再存一份等于埋一个会漂的副本。
            OptionSpec("objects", "list", required=True),
            OptionSpec("dimensions", "list", required=True),
            OptionSpec("scale", "list", required=True),
        ),
        check=_check_loop_rating,
        lower=_lower_loop_rating,
        side_columns=_loop_rating_columns,
    ),
    ThemeSpec(
        name="mjy-image-pk",
        label="图片 PK",
        requirement="R02-17",
        types=("T",),
        options=(
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            OptionSpec("items", "list", required=True),
            OptionSpec("pairs", "list", required=True),
        ),
        check=_check_image_pk,
        lower=_lower_image_pk,
        side_columns=_image_pk_columns,
    ),
    ThemeSpec(
        name="mjy-shelf",
        label="货架题",
        requirement="R02-18",
        types=("T",),
        options=(
            OptionSpec("structureVersion", "text", attribute=STRUCTURE_VERSION_ATTRIBUTE, required=True,
                       max_length=32, pattern=STRUCTURE_VERSION_PATTERN,
                       pattern_hint="必须以字母或数字开头，只含字母数字与 . _ -（副表契约 v1）"),
            OptionSpec("image", "text", attribute=SHELF_IMAGE_ATTRIBUTE, required=True,
                       max_length=MAX_IMAGE_LENGTH),
            OptionSpec("products", "list", required=True),
            OptionSpec("minPicks", "integer", attribute=MIN_ROWS_ATTRIBUTE, default=0,
                       minimum=0, maximum=HARD_MAX_ROWS),
            OptionSpec("maxPicks", "integer", attribute=MAX_ROWS_ATTRIBUTE, default=10,
                       minimum=1, maximum=HARD_MAX_ROWS),
            OptionSpec("maxQuantity", "integer", default=99, minimum=1, maximum=MAX_SHELF_QUANTITY),
        ),
        check=_check_shelf,
        lower=_lower_shelf,
        side_columns=_shelf_columns,
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
     "mjy_option_groups", "commented_checkbox"}
)
