"""切片 02.5 的副表题型：文字点睛（R02-22）、心理实验（R02-46）。

三类都**没有新增任何插件校验**：列全部落在切片 02.4 引入的通用列约束上
（``type: "enum"`` ＋ ``options``、``distinct``、整数上下限）。这正是当初
把那两条约束做成通用列约束、而不是给循环评价特制的回报。

骨架在 ``theme_kit.py``，通用列约束在 ``theme_columns.py``，注册表在 ``theme_specs.py``。
"""

import hashlib
from typing import Any, Dict, List, Tuple

from ..model import Question
from .theme_columns import _code_list, bounded_rows_issues
from .theme_kit import (
    COLUMNS_ATTRIBUTE, HARD_MAX_ROWS, HIGHLIGHT_SEGMENTS_ATTRIBUTE, HIGHLIGHT_TEXT_ATTRIBUTE, Issue,
    Lowering, MAX_COLUMN_OPTIONS, MAX_ROWS_ATTRIBUTE, MIN_ROWS_ATTRIBUTE, OPTION_VALUE,
    PSYCH_TRIALS_ATTRIBUTE, _int, canonical_json,
)

# ---------------------------------------------------------------- 文字点睛（R02-22）

#: 原文的长度上限（按字符）。再长的材料该拆成多道题，不该塞进一个题目属性。
MAX_HIGHLIGHT_TEXT = 4000
#: 片段取值代码里那段原文指纹的长度。十六进制 6 位＝2400 万分之一的碰撞面，
#: 而它只需要区分同一篇原文的前后两版，不是密码学用途。
_FINGERPRINT_LENGTH = 6


def _segment_code(start: int, length: int, piece: str) -> str:
    """片段的取值代码：偏移 ＋ 这一段原文自己的指纹。

    「中文标记偏移和原文版本一致」（R02-22）落在这里：代码里同时带着偏移与内容指纹，
    所以

    1. 改了这一段的字、或挪动了它的位置，代码就变，结构摘要跟着变，
       平台在重新发布时能发现「改了原文却没换结构版本」；
    2. 副表里存下来的单元格自己就说明「指向原文第几个字起的几个字、那几个字当时是什么」，
       读端不必回查当时的原文。

    留一个已知缺口：改动**最后一个片段之后**的文字不会改变任何代码。那种改动不会
    让已有的标记指错地方，所以不强制换版；映射表里如实记着。
    """
    digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()[:_FINGERPRINT_LENGTH]
    return "s{}_{}_{}".format(start, length, digest)


def _segment_issue(path: str, message: str) -> List[Issue]:
    return [(OPTION_VALUE, path, message)]


def _one_segment(raw: Any, path: str, text: str, cursor: int) -> Tuple[Dict[str, Any], List[Issue]]:
    """一个片段：起点（从 0 起的字符下标）＋ 长度，必须落在原文里且不与前一段重叠。"""
    if not isinstance(raw, dict):
        return {}, _segment_issue(path, "每一段都必须是对象")
    start, length = raw.get("start"), raw.get("length")
    if not _int(start) or not _int(length):
        return {}, _segment_issue(path, "start 与 length 都必须是整数")
    if start < 0 or length < 1 or start + length > len(text):
        return {}, _segment_issue(path, "片段必须落在原文之内（原文 {} 个字）".format(len(text)))
    if start < cursor:
        # 重叠或乱序会让「标了哪几个字」不再唯一，也会让两个片段共用一段原文。
        return {}, _segment_issue(path, "片段必须按偏移升序排列且互不重叠")
    piece = text[start:start + length]
    return {"code": _segment_code(start, length, piece), "label": piece,
            "start": start, "length": length}, []


def _highlight_segments(values: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Issue]]:
    text = values.get("text") or ""
    raw_segments = values.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        return [], _segment_issue("themeOptions.segments", "必须是非空数组")
    if len(raw_segments) > MAX_COLUMN_OPTIONS:
        return [], _segment_issue("themeOptions.segments", "最多 {} 段".format(MAX_COLUMN_OPTIONS))
    segments: List[Dict[str, Any]] = []
    issues: List[Issue] = []
    cursor = 0
    for index, raw in enumerate(raw_segments):
        segment, found = _one_segment(raw, "themeOptions.segments[{}]".format(index), text, cursor)
        issues.extend(found)
        if found:
            continue
        cursor = segment["start"] + segment["length"]
        segments.append(segment)
    return ([], issues) if issues else (segments, [])


def check_text_highlight(question: Question, values: Dict[str, Any]) -> List[Issue]:
    segments, issues = _highlight_segments(values)
    _tags, found = _code_list(values.get("tags"), "themeOptions.tags", MAX_COLUMN_OPTIONS)
    issues.extend(found)
    if issues:
        return issues
    # 片段列是唯一列，一个片段最多占一行——要求标的处数多过片段数就永远交不了卷。
    return bounded_rows_issues(values, "minMarks", "maxMarks", len(segments), "可标的片段数")


def text_highlight_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一行一处标记：标的是哪一段（枚举＋唯一），标成什么（枚举到声明过的标记）。

    「标了原文以外的东西」由枚举列自己挡住，「同一段标两次」由唯一列挡住——
    两条都是通用列约束，本类没动插件一行校验代码。
    """
    segments, _issues = _highlight_segments(values)
    tags, _found = _code_list(values.get("tags"), "themeOptions.tags", MAX_COLUMN_OPTIONS)
    return [
        {"code": "segment", "label": "标记的文字", "type": "enum", "required": True, "distinct": True,
         "options": [{"code": item["code"], "label": item["label"]} for item in segments]},
        {"code": "tag", "label": "标记", "type": "enum", "required": True, "options": tags},
    ]


def lower_text_highlight(question: Question, values: Dict[str, Any]) -> Lowering:
    segments, _issues = _highlight_segments(values)
    # 主题自己按偏移把原文切成可点的片段，所以这里只给代码与偏移，不再重复一份原文。
    spans = [{"code": item["code"], "start": item["start"], "length": item["length"]}
             for item in segments]
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(text_highlight_columns(values)),
        HIGHLIGHT_TEXT_ATTRIBUTE: values["text"],
        HIGHLIGHT_SEGMENTS_ATTRIBUTE: canonical_json(spans),
    })


# ---------------------------------------------------------------- 心理实验（R02-46）

#: 反应时的上限（毫秒）。十分钟以上的「反应」不是反应时，是这道题出了别的问题。
MAX_REACTION_MS = 600000
#: 刺激的描述（文字或素材地址）的长度上限。
MAX_STIMULUS_LENGTH = 500


def _psych_trials(values: Dict[str, Any]) -> Tuple[List[Dict[str, str]], List[Issue]]:
    """试次：代码＋标签＋刺激，可选的正确按键。

    **正确按键留在这里，不进列定义**：正确与否由平台按「试次的正确按键 vs 作答的按键」
    推导，让浏览器端提交一列 ``correct`` 等于让作答者自己宣布答对了。
    练习试次与没有对错的试次（偏好判断之类）可以不写正确按键。
    """
    trials, issues = _code_list(values.get("trials"), "themeOptions.trials", HARD_MAX_ROWS)
    if issues:
        return [], issues
    keys = {item["code"] for item in _code_list(values.get("keys"), "themeOptions.keys",
                                                MAX_COLUMN_OPTIONS)[0]}
    declared = values.get("trials") or []
    for index, (trial, raw) in enumerate(zip(trials, declared)):
        path = "themeOptions.trials[{}]".format(index)
        stimulus = raw.get("stimulus") if isinstance(raw, dict) else None
        if not isinstance(stimulus, str) or not stimulus.strip() or len(stimulus) > MAX_STIMULUS_LENGTH:
            issues.append((OPTION_VALUE, path + ".stimulus",
                           "每个试次都要有刺激，最长 {} 个字符".format(MAX_STIMULUS_LENGTH)))
            continue
        trial["stimulus"] = stimulus
        correct = raw.get("correct") if isinstance(raw, dict) else None
        if correct is None:
            continue
        if correct not in keys:
            issues.append((OPTION_VALUE, path + ".correct", "正确按键必须是声明过的按键之一"))
            continue
        trial["correct"] = correct
    return ([], issues) if issues else (trials, [])


def check_psych_trial(question: Question, values: Dict[str, Any]) -> List[Issue]:
    # 按键先单独判一次：试次的正确按键要对着它校验，按键列表坏掉时那一步没有依据。
    _keys, issues = _code_list(values.get("keys"), "themeOptions.keys", MAX_COLUMN_OPTIONS)
    if issues:
        return issues
    _trials, found = _psych_trials(values)
    return found


def psych_trial_columns(values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一行一个试次：哪一试次（枚举＋唯一）、按了哪个键（枚举）、用了多少毫秒（有界整数）。

    行数在 ``lower_psych_trial`` 里被钉死成试次个数，与循环评价同一套办法：
    枚举＋唯一＋行数三者合起来就是「每个试次恰好一行」。
    """
    trials, _issues = _psych_trials(values)
    keys, _found = _code_list(values.get("keys"), "themeOptions.keys", MAX_COLUMN_OPTIONS)
    return [
        {"code": "trial", "label": "试次", "type": "enum", "required": True, "distinct": True,
         "options": [{"code": item["code"], "label": item["label"]} for item in trials]},
        {"code": "key", "label": "按键", "type": "enum", "required": True,
         "options": [{"code": item["code"], "label": item["label"]} for item in keys]},
        {"code": "rt", "label": "反应时（毫秒）", "type": "integer", "required": True,
         "min": 0, "max": values.get("maxReactionMs", MAX_REACTION_MS)},
    ]


def lower_psych_trial(question: Question, values: Dict[str, Any]) -> Lowering:
    trials, _issues = _psych_trials(values)
    count = str(len(trials))
    return Lowering(attributes={
        COLUMNS_ATTRIBUTE: canonical_json(psych_trial_columns(values)),
        PSYCH_TRIALS_ATTRIBUTE: canonical_json(trials),
        # 每个试次各一行，不多不少：漏做一个试次不是「没答」，是这次实验不完整。
        MIN_ROWS_ATTRIBUTE: count,
        MAX_ROWS_ATTRIBUTE: count,
    })
