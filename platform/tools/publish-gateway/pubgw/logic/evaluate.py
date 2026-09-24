"""平台侧的 DSL 解释器：直接在语法树上按契约语义算出一份答卷的结果。

它是 WP-03.4「双执行比对」里平台那一次执行。另一次执行是真引擎跑编译产物
（emit.py 生成的 ExpressionScript）。两边算出来的值逐条比对，任何分歧都说明
**编译器**或**解释器**有一方错了——由比对用例自带的期望值当裁判（见
platform/tests/e2e/publish_gateway_parity.py）。

刻意只实现 platform/contracts/survey-logic-dsl-v1.md 第 4 节写下来的语义，
不去模仿 PHP 的类型杂耍：如果引擎在契约之外的角落有自己的脾气，那正是比对
要暴露的东西，不能靠两边一起装傻把它盖住。

值域：EMPTY（未作答或被隐藏）、bool、float、str。
"""

import math
from dataclasses import dataclass, field
from typing import Any, FrozenSet, List, Mapping, Optional

from . import ast
from .scope import BOOL, NUMBER, OTHER_CODE, SELECTED, WHOLE_SET, Resolved, Scope
from ..model import Question

#: 多选题「选中」在答卷里的值。
CHECKED = "Y"

#: 引擎读值时会先做 htmlSpecialCharsUserValue 的题型（自由文本与日期）。
_ESCAPED_TYPES = frozenset({"S", "T", "U", "D", "Q", ";"})
_ESCAPED_SUFFIXES = ("_other", "_comment")


class _Empty:
    """未作答或被条件隐藏。单例，故意不等于 0 也不等于 ""。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "EMPTY"

    def __bool__(self) -> bool:
        return False


EMPTY = _Empty()


class EvaluationError(ValueError):
    """解释器遇到没法算的树（只会在跳过类型检查时发生）。"""


@dataclass(frozen=True)
class AnswerSheet:
    """一次作答的快照。

    values 的键是引擎变量名（qcode 形式：``QAGE``、``QMULTI_SQ001``、``QPET_other``、
    ``QDUAL_R1_1``），值是答卷里存的原文；hidden 装的是被条件隐藏的**题目代码**。
    """

    values: Mapping[str, str] = field(default_factory=dict)
    hidden: FrozenSet[str] = frozenset()

    def raw(self, resolved: Resolved) -> str:
        if resolved.question.code in self.hidden:
            return ""
        return str(self.values.get(resolved.variable, "") or "")

    def raw_member(self, question_code: str, variable: str) -> str:
        if question_code in self.hidden:
            return ""
        return str(self.values.get(variable, "") or "")


def html_special_chars_user_value(text: str) -> str:
    """引擎 LimeExpressionManager::htmlSpecialCharsUserValue 的等价实现。"""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return escaped.replace("{", "&#123;").replace("}", "&#125;")


def render(value: Any) -> str:
    """规范化成文本，用于和引擎打印出来的结果逐字比对。"""
    if value is EMPTY:
        return ""
    if isinstance(value, bool):
        return "1" if value else ""
    if isinstance(value, (int, float)):
        return _number_text(float(value))
    return str(value)


def _number_text(number: float) -> str:
    """定点十位再去掉尾零：PHP 与 Python 的浮点默认打印精度不同，比对不该栽在这上面。"""
    if math.isnan(number) or math.isinf(number):
        return "NAN"
    text = "{:.10f}".format(number).rstrip("0").rstrip(".")
    return text if text not in ("", "-") else "0"


class Interpreter:
    """按契约语义求值。调用方保证树已经通过类型检查。"""

    def __init__(self, scope: Scope, owner: Optional[Question] = None):
        self._scope = scope
        self._owner = owner

    def evaluate(self, node: ast.Node, sheet: AnswerSheet) -> Any:
        visitor = _VISITORS.get(type(node))
        if visitor is None:
            raise EvaluationError("cannot evaluate {}".format(type(node).__name__))
        return visitor(self, node, sheet)

    # ------------------------------------------------------------- 引用

    def _resolve(self, node: ast.Node) -> Resolved:
        if isinstance(node, ast.SelfRef):
            if self._owner is None:
                raise EvaluationError("'self' used outside a validation rule")
            return self._scope.resolve_question(self._owner)
        return self._scope.resolve(node)

    def _reference(self, node: ast.Node, sheet: AnswerSheet) -> Any:
        resolved = self._resolve(node)
        if resolved.shape == SELECTED:
            return sheet.raw(resolved) == CHECKED
        if resolved.shape == WHOLE_SET:
            raise EvaluationError("a whole multiple-choice question has no single value")
        return self._value_of(resolved, sheet)

    def _value_of(self, resolved: Resolved, sheet: AnswerSheet) -> Any:
        raw = sheet.raw(resolved)
        if raw == "":
            return EMPTY
        kind = resolved.type.kind
        if kind == NUMBER:
            return _to_number(raw)
        if kind == BOOL:
            return raw == CHECKED
        # 文本、单选码与计算值（ANY）都按文本读；引擎读自由文本时先转义，这里照做。
        return _escape_if_needed(resolved, raw)

    # ------------------------------------------------------------- 字面量

    def _number(self, node: ast.Number, sheet: AnswerSheet) -> Any:
        return float(node.text)

    def _string(self, node: ast.String, sheet: AnswerSheet) -> Any:
        return node.value

    def _bool(self, node: ast.Bool, sheet: AnswerSheet) -> Any:
        return node.value

    # ------------------------------------------------------------- 运算

    def _unary(self, node: ast.Unary, sheet: AnswerSheet) -> Any:
        operand = self.evaluate(node.operand, sheet)
        if node.op == "not":
            return not _truth(operand)
        if operand is EMPTY:
            return EMPTY
        return -_number_of(operand)

    def _binary(self, node: ast.Binary, sheet: AnswerSheet) -> Any:
        if node.op in ast.BOOLEAN_OPERATORS:
            left = _truth(self.evaluate(node.left, sheet))
            if node.op == "and":
                return left and _truth(self.evaluate(node.right, sheet))
            return left or _truth(self.evaluate(node.right, sheet))
        left = self.evaluate(node.left, sheet)
        right = self.evaluate(node.right, sheet)
        if node.op in ast.ARITHMETIC_OPERATORS:
            return _arithmetic(node.op, left, right)
        if node.op in ast.EQUALITY_OPERATORS:
            equal = _equals(left, right)
            return equal if node.op == "==" else not equal
        return _ordering(node.op, left, right)

    def _in_list(self, node: ast.InList, sheet: AnswerSheet) -> Any:
        if node.container is not None:
            resolved = self._resolve(node.container)
            variable = "{}_{}".format(resolved.question.code, node.item.value)
            return sheet.raw_member(resolved.question.code, variable) == CHECKED
        item = self.evaluate(node.item, sheet)
        return any(_equals(item, self.evaluate(option, sheet)) for option in node.options)

    # ------------------------------------------------------------- 函数

    def _call(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        handler = _CALLS.get(node.name)
        if handler is None:
            raise EvaluationError("unknown function {!r}".format(node.name))
        return handler(self, node, sheet)

    def _raw_values(self, node: ast.Node, sheet: AnswerSheet) -> List[str]:
        """一个引用背后的全部答卷原文（多选题是它的每个选项）。"""
        resolved = self._resolve(node)
        if resolved.shape == WHOLE_SET:
            return [sheet.raw_member(resolved.question.code, member) for member in resolved.members]
        return [sheet.raw(resolved)]

    def _answered(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        return any(raw != "" for raw in self._raw_values(node.args[0], sheet))

    def _count(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        total = 0
        for argument in node.args:
            total += sum(1 for raw in self._raw_values(argument, sheet) if raw != "")
        return float(total)

    def _sum(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        total = 0.0
        for argument in node.args:
            value = self.evaluate(argument, sheet)
            total += 0.0 if value is EMPTY else _number_of(value)
        return total

    def _numeric(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        value = self.evaluate(node.args[0], sheet)
        if value is EMPTY:
            return EMPTY
        number = _number_of(value)
        if node.name == "abs":
            return abs(number)
        if node.name == "floor":
            return float(math.floor(number))
        if node.name == "ceil":
            return float(math.ceil(number))
        precision = int(float(node.args[1].text)) if len(node.args) == 2 else 0
        return _php_round(number, precision)

    def _if(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        branch = node.args[1] if _truth(self.evaluate(node.args[0], sheet)) else node.args[2]
        return self.evaluate(branch, sheet)

    def _coalesce(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        value = self.evaluate(node.args[0], sheet)
        return self.evaluate(node.args[1], sheet) if value is EMPTY else value

    def _length(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        return float(len(_text_of(self.evaluate(node.args[0], sheet))))

    def _join(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        return "".join(_text_of(self.evaluate(argument, sheet)) for argument in node.args)

    def _label(self, node: ast.Call, sheet: AnswerSheet) -> Any:
        resolved = self._resolve(node.args[0])
        code = sheet.raw(resolved)
        if code == "":
            return ""
        if code == OTHER_CODE:
            return sheet.raw_member(resolved.question.code, resolved.question.code + "_other")
        scale = _scale_of(resolved)
        for answer in resolved.question.answers_on_scale(scale):
            if answer.code == code:
                return answer.text
        return ""


# ------------------------------------------------------------------ 值语义


def _escape_if_needed(resolved: Resolved, raw: str) -> str:
    if resolved.question.type in _ESCAPED_TYPES or resolved.variable.endswith(_ESCAPED_SUFFIXES):
        return html_special_chars_user_value(raw)
    return raw


def _scale_of(resolved: Resolved) -> int:
    parts = resolved.variable.rsplit("_", 1)
    return int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0


def _to_number(raw: str) -> Any:
    try:
        return float(raw)
    except ValueError:
        return EMPTY


def _number_of(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text_of(value: Any) -> str:
    return render(value)


def _truth(value: Any) -> bool:
    if value is EMPTY:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return value not in ("", "0")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _arithmetic(op: str, left: Any, right: Any) -> Any:
    """契约 4「算术空值传播」：任一操作数为空、或除数为 0，结果就是空。"""
    if left is EMPTY or right is EMPTY:
        return EMPTY
    first, second = _number_of(left), _number_of(right)
    if op == "+":
        return first + second
    if op == "-":
        return first - second
    if op == "*":
        return first * second
    if second == 0:
        return EMPTY
    return first / second


def _equals(left: Any, right: Any) -> bool:
    if left is EMPTY or right is EMPTY:
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        return _truth(left) == _truth(right)
    if _is_number(left) and _is_number(right):
        return float(left) == float(right)
    return _text_of(left) == _text_of(right)


def _ordering(op: str, left: Any, right: Any) -> bool:
    if left is EMPTY or right is EMPTY:
        return False
    if _is_number(left) and _is_number(right):
        first, second = float(left), float(right)
    elif _is_number(left) or _is_number(right):
        return False
    else:
        first, second = _text_of(left), _text_of(right)
    if op == "<":
        return first < second
    if op == "<=":
        return first <= second
    if op == ">":
        return first > second
    return first >= second


def _php_round(number: float, precision: int) -> float:
    """PHP 的 round 是「四舍五入、逢五远离零」，Python 内建的是「逢五取偶」。"""
    factor = 10.0 ** precision
    scaled = number * factor
    rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return rounded / factor


_VISITORS = {
    ast.Number: Interpreter._number,
    ast.String: Interpreter._string,
    ast.Bool: Interpreter._bool,
    ast.Ref: Interpreter._reference,
    ast.SelfRef: Interpreter._reference,
    ast.Unary: Interpreter._unary,
    ast.Binary: Interpreter._binary,
    ast.InList: Interpreter._in_list,
    ast.Call: Interpreter._call,
}

_CALLS = {
    "answered": Interpreter._answered,
    "count": Interpreter._count,
    "sum": Interpreter._sum,
    "abs": Interpreter._numeric,
    "floor": Interpreter._numeric,
    "ceil": Interpreter._numeric,
    "round": Interpreter._numeric,
    "if": Interpreter._if,
    "coalesce": Interpreter._coalesce,
    "length": Interpreter._length,
    "join": Interpreter._join,
    "label": Interpreter._label,
}
