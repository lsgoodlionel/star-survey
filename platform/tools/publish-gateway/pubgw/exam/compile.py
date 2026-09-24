"""答案键 → 引擎（契约 survey-exam-v1 §4、§5）。

**只有一样产物**：一行 ``plugin_settings``（``MjyRuntimePolicy`` / ``mjy_exam_key``），
规范 JSON（键排序、紧凑、纯 ASCII），发布时按 ``digest``（SHA-256）回读核对。

与访问策略刻意不同的是：这里**没有 native_settings**。答案键不写进任何引擎原生
设置、任何题目属性、任何选项的 assessment_value、任何表达式——引擎渲染页面时
会碰到的每一样东西都不碰。理由见 disclosure.py 的说明。

条目按题目代码排序：同一份答案键无论作者怎么排，编译出的摘要都一样。
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..model import SurveyDefinition
from .schema import CHOICE, SET, ExamKey, parse_exam

PLUGIN_NAME = "MjyRuntimePolicy"
EXAM_KEY = "mjy_exam_key"
PAYLOAD_SCHEMA = "mjy-exam-key/1"


@dataclass(frozen=True)
class CompiledExam:
    payload: str
    digest: str


def compile_exam(definition: SurveyDefinition) -> Optional[CompiledExam]:
    """定义没有 exam 块返回 None。调用方保证定义已通过校验。"""
    key = parse_exam(definition)
    if key is None or not key.entries:
        return None
    text = json.dumps(_payload(key), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return CompiledExam(payload=text, digest=hashlib.sha256(text.encode("ascii")).hexdigest())


def _payload(key: ExamKey) -> Dict[str, Any]:
    return {
        "schema": PAYLOAD_SCHEMA,
        "answerKey": [_entry(entry) for entry in sorted(key.entries, key=lambda item: item.code)],
    }


def _entry(entry) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "question": entry.code,
        "kind": entry.kind,
        "correct": list(entry.correct),
        "points": entry.points,
    }
    if entry.kind not in (CHOICE, SET):
        # 文本与数值题才有匹配口径；选项代码是精确匹配，写进去只会让摘要多一份噪音。
        payload["match"] = {"ignoreCase": entry.ignore_case, "trim": entry.trim}
    return payload
