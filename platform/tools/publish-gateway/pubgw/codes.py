r"""引擎的代码合法性规则（镜像自 application/models/Question.php 与 Answer.php）。

为什么要在平台侧复制一遍：导入端在代码非法或冲突时会**自动改名**
（import_helper.php:2646-2668、:2798-2822），改名之后平台的
「题目 UUID ↔ 代码」映射就与引擎不一致，而引擎不会报错。与其事后回滚，
不如发布前就按引擎自己的规则挡住。

两处刻意比引擎更严：

1. 题目代码。引擎的模式是 ``/^[a-z,A-Z][[:alnum:]]*$/``，字符组里的逗号
   是笔误，结果首字符允许写成 ``,``。平台只允许字母开头。
2. 子题代码。引擎的模式是 ``/^[a-zA-z0-9]*$/``，``A-z`` 覆盖了
   ``[ \ ] ^ _ ` ``，而且允许空串。平台只允许非空的字母数字。
"""

import re
from typing import Optional

QUESTION_CODE_MAX_LENGTH = 20
ANSWER_CODE_MAX_LENGTH = 5

_QUESTION_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_ALNUM_PATTERN = re.compile(r"^[A-Za-z0-9]+$")

#: ExpressionManager 的保留字（Question.php:276-284）。
RESERVED_QUESTION_CODES = frozenset(
    {"LANG", "SID", "SAVEDID", "TOKEN", "QID", "GID", "SGQ", "self", "that", "this"}
)

#: 子题保留字（Question.php:288）。
RESERVED_SUBQUESTION_CODE = "time"

INVALID = "invalid"
TOO_LONG = "too_long"
RESERVED = "reserved"


def check_question_code(code: str) -> Optional[str]:
    """合法返回 None，否则返回问题种类。"""
    if len(code) > QUESTION_CODE_MAX_LENGTH:
        return TOO_LONG
    if not _QUESTION_CODE_PATTERN.match(code):
        return INVALID
    if code in RESERVED_QUESTION_CODES:
        return RESERVED
    return None


def check_subquestion_code(code: str, parent_type: str, parent_has_other: bool) -> Optional[str]:
    if len(code) > QUESTION_CODE_MAX_LENGTH:
        return TOO_LONG
    if not _ALNUM_PATTERN.match(code):
        return INVALID
    if code.lower() == RESERVED_SUBQUESTION_CODE:
        return RESERVED
    if parent_has_other and code.lower() == "other":
        return RESERVED
    if parent_type == "P" and code.lower().endswith("comment"):
        return RESERVED
    return None


def check_answer_code(code: str) -> Optional[str]:
    if len(code) > ANSWER_CODE_MAX_LENGTH:
        return TOO_LONG
    if not _ALNUM_PATTERN.match(code):
        return INVALID
    return None
