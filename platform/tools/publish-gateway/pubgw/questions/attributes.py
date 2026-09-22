"""已知题目属性的取值校验。

v1 起 ``attributes`` 就是原样透传给引擎的字符串表；这里只检查**认识的**属性的取值
（数字、日期、上下限次序、上传扩展名），不认识的属性照旧透传——收紧成白名单会让
已经能发布的旧定义突然失败。引擎导入时不校验这些值：写坏的 ``max_answers`` 会让题目
永远无法提交，写坏的 ``date_min`` 会让表达式报错后被引擎当成「无限制」。
"""

import datetime
import re
from typing import Callable, Dict, List, Optional, Tuple

from ..model import Question

VALUE = "E_ATTRIBUTE_VALUE"
RANGE = "E_ATTRIBUTE_RANGE"
FILETYPE_FORBIDDEN = "E_UPLOAD_FILETYPE_FORBIDDEN"
FILETYPE_REQUIRED = "E_UPLOAD_FILETYPE_REQUIRED"

#: maxLength 与 maximum_chars 的上限：MySQL 的 text 列 65,535 字节，utf8mb4 每字最多 4 字节。
MAX_CHARS_LIMIT = 16000
MAX_FILES_LIMIT = 10
#: 单个文件上限 100 MiB（引擎属性单位是 KB）。
MAX_FILESIZE_KB = 102400

#: 上传题绝不放行的扩展名：能被 Web 服务器执行或被浏览器当页面渲染的。
FORBIDDEN_EXTENSIONS = frozenset(
    {
        "php", "php3", "php4", "php5", "php7", "phtml", "phar", "pht", "phps", "inc",
        "asp", "aspx", "jsp", "jspx", "cgi", "pl", "py", "rb", "sh", "bash", "bat", "cmd", "com",
        "exe", "dll", "msi", "js", "mjs", "html", "htm", "xhtml", "shtml", "svg", "xml", "htaccess",
    }
)

_NUMBER = re.compile(r"\A-?\d+(\.\d+)?\Z")
_INTEGER = re.compile(r"\A\d+\Z")
_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_EXTENSION = re.compile(r"\A[a-z0-9]{1,10}\Z")

Issue = Tuple[str, str, str]  # (错误码, 属性名, 消息)


def _is_integer(value: str) -> bool:
    return bool(_INTEGER.match(value.strip()))


def _is_number(value: str) -> bool:
    return bool(_NUMBER.match(value.strip()))


def _is_date(value: str) -> bool:
    text = value.strip()
    if not _DATE.match(text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


_CHOICE_COUNT_TYPES = frozenset({"M", "P"})
_NUMERIC_TYPES = frozenset({"N", "K"})
_TEXT_TYPES = frozenset({"S", "T", "U", "Q"})


def _option_count(question: Question) -> int:
    return len(question.subquestions) + (1 if question.other else 0)


def _check_answer_counts(question: Question) -> List[Issue]:
    if question.type not in _CHOICE_COUNT_TYPES:
        return []
    issues: List[Issue] = []
    bounds: Dict[str, int] = {}
    for name in ("min_answers", "max_answers"):
        raw = question.attributes.get(name)
        if raw is None or raw.strip() == "":
            continue
        if not _is_integer(raw):
            issues.append((VALUE, name, "{} 必须是非负整数，得到 {!r}".format(name, raw)))
            continue
        bounds[name] = int(raw)
    if "max_answers" in bounds and bounds["max_answers"] > _option_count(question):
        issues.append((RANGE, "max_answers", "max_answers 超过了可选项数 {}".format(_option_count(question))))
    if bounds.get("min_answers", 0) > bounds.get("max_answers", bounds.get("min_answers", 0)):
        issues.append((RANGE, "min_answers", "min_answers 不能大于 max_answers"))
    return issues


def _check_numeric(question: Question) -> List[Issue]:
    if question.type not in _NUMERIC_TYPES:
        return []
    issues: List[Issue] = []
    bounds: Dict[str, float] = {}
    for name in ("min_num_value_n", "max_num_value_n", "equals_num_value"):
        raw = question.attributes.get(name)
        if raw is None or raw.strip() == "":
            continue
        if not _is_number(raw):
            issues.append((VALUE, name, "{} 必须是数字字面量（不接受表达式），得到 {!r}".format(name, raw)))
            continue
        bounds[name] = float(raw)
    if "min_num_value_n" in bounds and "max_num_value_n" in bounds and bounds["min_num_value_n"] > bounds["max_num_value_n"]:
        issues.append((RANGE, "min_num_value_n", "min_num_value_n 不能大于 max_num_value_n"))
    flag = question.attributes.get("num_value_int_only")
    if flag is not None and flag not in ("0", "1"):
        issues.append((VALUE, "num_value_int_only", "num_value_int_only 只能是 0 或 1"))
    return issues


def _check_text(question: Question) -> List[Issue]:
    raw = question.attributes.get("maximum_chars")
    if raw is None or question.type not in _TEXT_TYPES | _NUMERIC_TYPES:
        return []
    if not _is_integer(raw):
        return [(VALUE, "maximum_chars", "maximum_chars 必须是正整数")]
    if not 1 <= int(raw) <= MAX_CHARS_LIMIT:
        return [(RANGE, "maximum_chars", "maximum_chars 必须在 1 到 {} 之间".format(MAX_CHARS_LIMIT))]
    return []


def _check_dates(question: Question) -> List[Issue]:
    if question.type != "D":
        return []
    issues: List[Issue] = []
    dates: Dict[str, str] = {}
    for name in ("date_min", "date_max"):
        raw = question.attributes.get(name)
        if raw is None or raw.strip() == "":
            continue
        if not _is_date(raw):
            issues.append((VALUE, name, "{} 必须是存在的日期 YYYY-MM-DD（不接受表达式），得到 {!r}".format(name, raw)))
            continue
        dates[name] = raw.strip()
    if len(dates) == 2 and dates["date_min"] > dates["date_max"]:
        issues.append((RANGE, "date_min", "date_min 不能晚于 date_max"))
    return issues


def _check_ranking(question: Question) -> List[Issue]:
    raw = question.attributes.get("max_subquestions")
    if question.type != "R" or raw is None:
        return []
    if not _is_integer(raw):
        return [(VALUE, "max_subquestions", "max_subquestions 必须是正整数")]
    if not 1 <= int(raw) <= len(question.subquestions):
        return [(RANGE, "max_subquestions", "max_subquestions 必须在 1 到排序项数之间")]
    return []


def _bounded_integer(question: Question, name: str, low: int, high: int) -> Tuple[Optional[int], List[Issue]]:
    raw = question.attributes.get(name)
    if raw is None or raw.strip() == "":
        return None, []
    if not _is_integer(raw):
        return None, [(VALUE, name, "{} 必须是整数".format(name))]
    if not low <= int(raw) <= high:
        return None, [(RANGE, name, "{} 必须在 {} 到 {} 之间".format(name, low, high))]
    return int(raw), []


def _check_upload(question: Question) -> List[Issue]:
    if question.type != "|":
        return []
    issues: List[Issue] = []
    _, found = _bounded_integer(question, "max_filesize", 1, MAX_FILESIZE_KB)
    issues.extend(found)
    most, found = _bounded_integer(question, "max_num_of_files", 1, MAX_FILES_LIMIT)
    issues.extend(found)
    least, found = _bounded_integer(question, "min_num_of_files", 0, MAX_FILES_LIMIT)
    issues.extend(found)
    if least is not None and most is not None and least > most:
        issues.append((RANGE, "min_num_of_files", "min_num_of_files 不能大于 max_num_of_files"))
    raw = question.attributes.get("allowed_filetypes")
    if raw is None or raw.strip() == "":
        # 缺省时引擎用题型主题里的默认清单；平台要求写显式值，同一份包在任何实例上放行的扩展名都一样。
        issues.append((FILETYPE_REQUIRED, "allowed_filetypes", "上传题必须显式列出允许的扩展名"))
    else:
        issues.extend(_check_extensions(raw))
    return issues


def _check_extensions(raw: str) -> List[Issue]:
    extensions = [item.strip().lower() for item in raw.split(",")]
    if not all(_EXTENSION.match(item) for item in extensions):
        return [(VALUE, "allowed_filetypes", "allowed_filetypes 是逗号分隔的扩展名（字母数字，最长 10 位）")]
    forbidden = sorted(set(extensions) & FORBIDDEN_EXTENSIONS)
    if forbidden:
        return [(FILETYPE_FORBIDDEN, "allowed_filetypes", "不允许上传可执行或可渲染的文件：{}".format(", ".join(forbidden)))]
    return []


def _check_star_rating(question: Question) -> List[Issue]:
    raw = question.attributes.get("slider_rating")
    if question.type != "5" or raw is None or raw in ("0", "1", "2"):
        return []
    return [(VALUE, "slider_rating", "slider_rating 只能是 0（按钮）、1（星级）或 2（滑块）")]


def _check_exclusive_codes(question: Question) -> List[Issue]:
    raw = question.attributes.get("exclude_all_others")
    if question.type not in _CHOICE_COUNT_TYPES or raw is None or raw.strip() == "":
        return []
    codes = {sub.code for sub in question.subquestions}
    unknown = [code for code in (item.strip() for item in raw.split(";")) if code not in codes]
    if unknown:
        return [(VALUE, "exclude_all_others", "exclude_all_others 里的 {} 不是本题的子题代码".format(", ".join(unknown)))]
    return []


_CHECKS: Tuple[Callable[[Question], List[Issue]], ...] = (
    _check_answer_counts, _check_numeric, _check_text, _check_dates, _check_ranking,
    _check_upload, _check_star_rating, _check_exclusive_codes,
)


def check_attributes(question: Question) -> List[Issue]:
    issues: List[Issue] = []
    for check in _CHECKS:
        issues.extend(check(question))
    return issues
