"""题型主题测试共用的样例题与断言助手。

每个主题一个构造器，默认值就是「一道能发布的合法题」，各用例只覆盖自己关心的那一项。
``ALL_THEMED`` 把它们凑齐，用来证明「每个注册过的主题在它自己的题型上都能通过」。
"""

import dataclasses
import json
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from pubgw.compiler import LssCompiler
from pubgw.questions.themes import VIEW_FOLDERS
from pubgw.validate import validate_definition

from .qtype_fixtures import answer, definition_with, question, sub

TABLE_COLUMNS = [
    {"code": "item", "label": "物品", "type": "text", "required": True, "maxLength": 40},
    {"code": "qty", "label": "数量", "type": "integer", "required": True, "min": 1, "max": 99},
]


#: 多级下拉用的那本字典快照（北京 / 广东两棵子树）。
#: 平台在发布时把它物化进定义，所以任何带多级下拉的样例定义都必须带着它。
DICTIONARY_SNAPSHOT = {
    "code": "cn-admin-divisions",
    "version": "2024.1",
    "digest": "dg1:0123456789abcdef",
    "nodes": [
        ["110000", "", "北京市"],
        ["110100", "110000", "市辖区"],
        ["110101", "110100", "东城区"],
        ["440000", "", "广东省"],
        ["440100", "440000", "广州市"],
        ["440103", "440100", "荔湾区"],
    ],
}


def themed_definition(*questions, version=1):
    """样例定义；里面有多级下拉时自动带上它引用的字典快照。"""
    payload = definition_with(*questions, version=version)
    if any(item.get("theme") == "mjy-cascading-select" for item in questions):
        return dataclasses.replace(payload, dictionaries=(DICTIONARY_SNAPSHOT,))
    return payload


def issues(*questions, version=1):
    report = validate_definition(themed_definition(*questions, version=version))
    return [(issue.code, issue.path) for issue in report.issues]


def codes(*questions, version=1):
    return [code for code, _ in issues(*questions, version=version)]


def compile_attributes(*questions, code="Q1"):
    tree = ElementTree.fromstring(LssCompiler().compile(themed_definition(*questions)).lss)
    rows = [{child.tag: (child.text or "") for child in row}
            for row in tree.findall("question_attributes/rows/row")]
    qids = {row.find("title").text: row.find("qid").text
            for row in tree.findall("questions/rows/row")}
    return {row["attribute"]: row["value"] for row in rows if row["qid"] == qids[code]}


def theme_row(*questions, code="Q1"):
    tree = ElementTree.fromstring(LssCompiler().compile(themed_definition(*questions)).lss)
    for row in tree.findall("questions/rows/row"):
        if row.find("title").text == code:
            node = row.find("question_theme_name")
            return "" if node is None or node.text is None else node.text
    raise AssertionError("no question row for " + code)


# ------------------------------------------------------------------ 各主题的样例题


def collapsible(**options):
    payload = {"summary": "第二部分：家庭情况"}
    payload.update(options)
    return question("X", code="QSEC", theme="mjy-collapsible", themeOptions=payload)


def scan(max_length=64, **options):
    payload = dict(options)
    extra = {"theme": "mjy-scan-input", "themeOptions": payload}
    if max_length is not None:
        extra["maxLength"] = max_length
    return question("S", code="QSCAN", **extra)


def grouped(qtype="L", **options):
    payload = {"groups": [{"label": "水果", "codes": ["A1", "A2"]}, {"label": "蔬菜", "codes": ["A3"]}]}
    payload.update(options)
    extra = {"theme": "mjy-grouped-options", "themeOptions": payload}
    if qtype == "L":
        extra["answers"] = [answer("A1"), answer("A2"), answer("A3")]
    else:
        extra["subquestions"] = [sub("A1"), sub("A2"), sub("A3")]
    return question(qtype, code="QGRP", **extra)


def stepper(**options):
    return question("F", code="QSTEP", theme="mjy-matrix-stepper", themeOptions=dict(options),
                    answers=[answer("A1"), answer("A2")], subquestions=[sub("R1"), sub("R2")])


def inline_blank(**options):
    payload = {"blankMaxLength": 30}
    payload.update(options)
    return question("P", code="QBLANK", theme="mjy-inline-blank", themeOptions=payload,
                    subquestions=[sub("S1"), sub("S2")])


def table(**options):
    payload = {"structureVersion": "rt1", "columns": TABLE_COLUMNS, "minRows": 1, "maxRows": 5}
    payload.update(options)
    return question("T", code="QTABLE", theme="mjy-repeating-table", themeOptions=payload)


def heatmap(**options):
    payload = {"structureVersion": "hm1", "image": "https://assets.example.invalid/store.png", "maxPoints": 3}
    payload.update(options)
    return question("T", code="QHEAT", theme="mjy-heatmap", themeOptions=payload)


LOOP_OBJECTS = [{"code": "B1", "label": "甲品牌"}, {"code": "B2", "label": "乙品牌"}]
LOOP_DIMENSIONS = [{"code": "price", "label": "价格"}, {"code": "service", "label": "服务"}]
LOOP_SCALE = [{"code": "1", "label": "差"}, {"code": "2", "label": "一般"}, {"code": "3", "label": "好"}]


def loop_rating(**options):
    payload = {"structureVersion": "lr1", "objects": LOOP_OBJECTS,
               "dimensions": LOOP_DIMENSIONS, "scale": LOOP_SCALE}
    payload.update(options)
    return question("T", code="QLOOP", theme="mjy-loop-rating", themeOptions=payload)


PK_ITEMS = [
    {"code": "A", "label": "包装甲", "image": "https://assets.example.invalid/a.png"},
    {"code": "B", "label": "包装乙", "image": "https://assets.example.invalid/b.png"},
    {"code": "C", "label": "包装丙", "image": "https://assets.example.invalid/c.png"},
]
PK_PAIRS = [{"code": "P1", "left": "A", "right": "B"}, {"code": "P2", "left": "B", "right": "C"}]


def image_pk(**options):
    payload = {"structureVersion": "pk1", "items": PK_ITEMS, "pairs": PK_PAIRS}
    payload.update(options)
    return question("T", code="QPK", theme="mjy-image-pk", themeOptions=payload)


SHELF_PRODUCTS = [
    {"code": "S1", "label": "牛奶", "x": 0.1, "y": 0.2, "w": 0.2, "h": 0.3},
    {"code": "S2", "label": "面包", "x": 0.5, "y": 0.2, "w": 0.2, "h": 0.3},
]


def shelf(**options):
    payload = {"structureVersion": "sh1", "image": "https://assets.example.invalid/shelf.png",
               "products": SHELF_PRODUCTS, "maxPicks": 3}
    payload.update(options)
    return question("T", code="QSHELF", theme="mjy-shelf", themeOptions=payload)


# ------------------------------------------------------------------ 切片 02.5 的三类


#: 原文（R02-22）。段落偏移按**字符**计，与下面的 segments 一一对应。
HIGHLIGHT_TEXT = "苹果很甜，香蕉太软，梨子刚好。"
#: 「苹果很甜」「香蕉太软」「梨子刚好」：互不重叠、按偏移升序。
HIGHLIGHT_SEGMENTS = [{"start": 0, "length": 4}, {"start": 5, "length": 4}, {"start": 10, "length": 4}]
HIGHLIGHT_TAGS = [{"code": "like", "label": "喜欢"}, {"code": "dislike", "label": "不喜欢"}]


def text_highlight(**options):
    payload = {"structureVersion": "th1", "text": HIGHLIGHT_TEXT,
               "segments": HIGHLIGHT_SEGMENTS, "tags": HIGHLIGHT_TAGS, "maxMarks": 3}
    payload.update(options)
    return question("T", code="QMARK", theme="mjy-text-highlight", themeOptions=payload)


#: 试次（R02-46）。``correct`` 是**正确按键**，随定义留痕，不进列定义：
#: 正确与否由平台按声明推导，作答者提交不了「我答对了」。
PSYCH_TRIALS = [
    {"code": "T1", "label": "第一试次", "stimulus": "红", "correct": "left"},
    {"code": "T2", "label": "第二试次", "stimulus": "蓝", "correct": "right"},
]
PSYCH_KEYS = [{"code": "left", "label": "左键 F"}, {"code": "right", "label": "右键 J"}]


def psych_trial(**options):
    payload = {"structureVersion": "ps1", "trials": PSYCH_TRIALS, "keys": PSYCH_KEYS,
               "maxReactionMs": 5000}
    payload.update(options)
    return question("T", code="QPSY", theme="mjy-psych-trial", themeOptions=payload)


#: KANO 的功能点（R02-47）。量表不在这里——它由模型固定，作者写不了。
KANO_FEATURES = [{"code": "F1", "label": "夜间模式"}, {"code": "F2", "label": "离线缓存"}]


def kano(**options):
    payload = {"structureVersion": "kn1", "features": KANO_FEATURES}
    payload.update(options)
    return question("T", code="QKANO", theme="mjy-model-kano", themeOptions=payload)


def cascading_select(**options):
    """R02-03：引用一本字典的某一版，三级。版本与摘要是平台发布时固化下来的。"""
    payload = {
        "structureVersion": "r1",
        "dictionary": "cn-admin-divisions",
        "dictionaryVersion": "2024.1",
        "dictionaryDigest": "dg1:0123456789abcdef",
        "levels": ["省", "市", "区"],
    }
    payload.update(options)
    return question("T", code="QREGION", theme="mjy-cascading-select", themeOptions=payload)
# ------------------------------------------------------------------ 切片 02.6 的媒体类


#: 平台在发布时写进定义的取件地址形状（ADR 0019 决定 4）：路径带租户 / 资产 / 版本，
#: 查询串带过期时刻与签名。样例里的值只要形状对就行，签名本身由平台侧的用例覆盖。
ASSET_URL = "https://survey.example/a/9f1c.../{}/1?exp=1800000000&sig=AAAA"


#: 「这个参数没传」与「这个参数传的是 None（＝把键去掉）」要分得开。
_DEFAULT = object()

_SLIDE_LABELS = {"A1": "包装甲", "A2": "包装乙"}


def slide(code, url=_DEFAULT, alt=_DEFAULT, version=1):
    """一张幻灯片：对准一个选项代码，带平台签发的地址、替代文本与被钉死的资产版本号。

    ``url``／``version`` 传 ``None`` 表示**把这个键去掉**，用来构造缺字段的用例；
    ``alt`` 传空串则保留这个键但留空（替代文本必填的用例靠它）。
    """
    payload = {"code": code}
    address = ASSET_URL.format(code.lower()) if url is _DEFAULT else url
    if address is not None:
        payload["url"] = address
    payload["alt"] = _SLIDE_LABELS.get(code, code) if alt is _DEFAULT else alt
    if version is not None:
        payload["assetVersion"] = version
    return payload


def carousel(qtype="L", slides=_DEFAULT, attributes=None, **options):
    """R02-20 轮播图：原生单选＋主题，一张幻灯片对一个选项。"""
    payload = {"slides": [slide("A1"), slide("A2")] if slides is _DEFAULT else slides}
    if payload["slides"] is None:
        del payload["slides"]
    payload.update(options)
    extra = {"theme": "mjy-carousel", "themeOptions": payload,
             "answers": [answer("A1"), answer("A2")]}
    if attributes:
        extra["attributes"] = attributes
    return question(qtype, code="QCARO", **extra)


ALL_THEMED = (collapsible(), scan(), grouped(), stepper(), inline_blank(), table(), heatmap(),
              loop_rating(), image_pk(), shelf(), text_highlight(), psych_trial(), kano(),
              cascading_select(), carousel())


# ------------------------------------------------------------------ 注册表


THEME_ROOT = Path(__file__).resolve().parents[4] / "themes/question"


def view_folder(theme, qtype):
    return THEME_ROOT / theme / "survey/questions/answer" / VIEW_FOLDERS[qtype]
