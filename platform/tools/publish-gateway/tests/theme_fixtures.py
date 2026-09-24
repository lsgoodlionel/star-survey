"""题型主题测试共用的样例题与断言助手。

每个主题一个构造器，默认值就是「一道能发布的合法题」，各用例只覆盖自己关心的那一项。
``ALL_THEMED`` 把它们凑齐，用来证明「每个注册过的主题在它自己的题型上都能通过」。
"""

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


def issues(*questions, version=1):
    report = validate_definition(definition_with(*questions, version=version))
    return [(issue.code, issue.path) for issue in report.issues]


def codes(*questions, version=1):
    return [code for code, _ in issues(*questions, version=version)]


def compile_attributes(*questions, code="Q1"):
    tree = ElementTree.fromstring(LssCompiler().compile(definition_with(*questions)).lss)
    rows = [{child.tag: (child.text or "") for child in row}
            for row in tree.findall("question_attributes/rows/row")]
    qids = {row.find("title").text: row.find("qid").text
            for row in tree.findall("questions/rows/row")}
    return {row["attribute"]: row["value"] for row in rows if row["qid"] == qids[code]}


def theme_row(*questions, code="Q1"):
    tree = ElementTree.fromstring(LssCompiler().compile(definition_with(*questions)).lss)
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


ALL_THEMED = (collapsible(), scan(), grouped(), stepper(), inline_blank(), table(), heatmap(),
              loop_rating(), image_pk(), shelf(), text_highlight(), psych_trial(), kano())


# ------------------------------------------------------------------ 注册表


THEME_ROOT = Path(__file__).resolve().parents[4] / "themes/question"


def view_folder(theme, qtype):
    return THEME_ROOT / theme / "survey/questions/answer" / VIEW_FOLDERS[qtype]
