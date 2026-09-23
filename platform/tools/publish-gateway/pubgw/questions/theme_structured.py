"""切片 02.3–02.4 的副表题型：循环评价、图片 PK、货架题与热力图的列生成器。

作答以 JSON 信封存进基础题型的那一列，列定义由平台按 ``themeOptions`` 生成，
作者写不了；插件按列定义把它投影进副表
（platform/contracts/question-extension-tables-v1.md）。

骨架在 ``theme_kit.py``，通用列约束在 ``theme_columns.py``，注册表在 ``theme_specs.py``。
"""

from typing import Any, Dict, List, Tuple

from ..model import Question
from .theme_columns import _code_list, bounded_rows_issues
from .theme_kit import (
    COLUMNS_ATTRIBUTE, HARD_MAX_ROWS, Issue, LOOP_OBJECTS_ATTRIBUTE, Lowering, MAX_COLUMNS,
    MAX_COLUMN_OPTIONS, MAX_ROWS_ATTRIBUTE, MIN_ROWS_ATTRIBUTE, OPTION_VALUE, PK_ITEMS_ATTRIBUTE,
    PK_PAIRS_ATTRIBUTE, SHELF_PRODUCTS_ATTRIBUTE, _COLUMN_CODE_PATTERN, canonical_json,
)

#: 图片 PK 与货架题的图片地址上限。
MAX_IMAGE_LENGTH = 500

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


def check_loop_rating(question: Question, values: Dict[str, Any]) -> List[Issue]:
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


def loop_rating_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一行一个评价对象：第一列认对象（枚举＋唯一），其余每个维度一列（枚举到量表）。

    行数在 ``lower_loop_rating`` 里被钉死成对象个数，「枚举＋唯一＋行数」三者
    合起来就逼出「每个对象恰好评一次」，不必再写一套按行下标的规则。
    """
    objects, dimensions, scale, _issues = _loop_lists(values)
    columns = [{"code": LOOP_OBJECT_COLUMN, "label": "评价对象", "type": "enum",
                "required": True, "options": objects, "distinct": True}]
    columns.extend({"code": item["code"], "label": item["label"], "type": "enum",
                    "required": True, "options": scale} for item in dimensions)
    return columns


def lower_loop_rating(question: Question, values: Dict[str, Any]) -> Lowering:
    objects, _dimensions, _scale, _issues = _loop_lists(values)
    count = str(len(objects))
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(loop_rating_columns(values)),
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


def check_image_pk(question: Question, values: Dict[str, Any]) -> List[Issue]:
    _items, _pairs, issues = _pk_lists(values)
    return issues


def image_pk_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
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


def lower_image_pk(question: Question, values: Dict[str, Any]) -> Lowering:
    items, pairs, _issues = _pk_lists(values)
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(image_pk_columns(values)),
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


def check_shelf(question: Question, values: Dict[str, Any]) -> List[Issue]:
    products, issues = _shelf_products(values)
    if issues:
        return issues
    # 商品列是唯一列，一件商品最多占一行——要求取的件数多过货架上的商品就永远交不了卷。
    return bounded_rows_issues(values, "minPicks", "maxPicks", len(products), "货架上的商品数")


def shelf_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
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


def lower_shelf(question: Question, values: Dict[str, Any]) -> Lowering:
    products, _issues = _shelf_products(values)
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(shelf_columns(values)),
        SHELF_PRODUCTS_ATTRIBUTE: canonical_json(products),
    })


# ---------------------------------------------------------------- 热力图选区（R02-19）


def heatmap_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """归一化坐标：两列 decimal，范围写死在 0…1，服务端由插件逐格校验。"""
    return [
        {"code": "x", "label": "横向比例", "type": "decimal", "required": True, "min": 0, "max": 1},
        {"code": "y", "label": "纵向比例", "type": "decimal", "required": True, "min": 0, "max": 1},
    ]


def lower_heatmap(question: Question, values: Dict[str, Any]) -> Lowering:
    return Lowering(attributes={COLUMNS_ATTRIBUTE: canonical_json(heatmap_columns(values))})
