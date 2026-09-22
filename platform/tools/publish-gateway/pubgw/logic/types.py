"""类型检查：每个节点推出一个类型，不合规的地方记一条 Finding 并以 ANY 继续，
这样一个表达式里的多处错误能一次报全，而不会一错引发一串连锁错误。
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import ast
from .scope import (
    ANY,
    BOOL,
    CHOICE,
    NUMBER,
    TEXT,
    TYPE_ANY,
    TYPE_BOOL,
    TYPE_NUMBER,
    TYPE_TEXT,
    WHOLE_SET,
    Resolved,
    ResolutionError,
    Scope,
    Type,
)
from ..model import Question

#: 表达式出现的位置决定它要什么类型、能不能用 self。
CONDITION = "condition"
GROUP_CONDITION = "group_condition"
VALIDATION = "validation"
CALCULATION = "calculation"
PIPE = "pipe"

_BOOLEAN_KINDS = frozenset({CONDITION, GROUP_CONDITION, VALIDATION})

#: 字符串字面量里禁止的字符。引擎会把文本答案的 <>&{} 转义后再拿来比较
#: （LimeExpressionManager::htmlSpecialCharsUserValue），关系表达式在解析前还会
#: 做一次 htmlspecialchars_decode；反斜杠会和引擎的字符串转义纠缠。一律拒绝。
FORBIDDEN_STRING_CHARS = frozenset("{}<>&\\")


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    pos: int


@dataclass(frozen=True)
class Context:
    kind: str
    owner: Optional[Question] = None


@dataclass
class Analysis:
    type: Type = TYPE_ANY
    references: List[Tuple[Question, int]] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)


def check_expression(node: ast.Node, scope: Scope, context: Context) -> Analysis:
    checker = _Checker(scope, context)
    result = checker.visit(node)
    checker.require_result(result, node)
    return Analysis(result, checker.references, checker.findings)


class _Checker:
    def __init__(self, scope: Scope, context: Context):
        self._scope = scope
        self._context = context
        self.references: List[Tuple[Question, int]] = []
        self.findings: List[Finding] = []

    # ------------------------------------------------------------- 工具

    def fail(self, code: str, message: str, pos: int) -> Type:
        self.findings.append(Finding(code, message, pos))
        return TYPE_ANY

    def require_result(self, result: Type, node: ast.Node) -> None:
        kind = self._context.kind
        if kind in _BOOLEAN_KINDS and result.kind not in (BOOL, ANY):
            self.fail("E_EXPR_TYPE", "a {} must be true/false, got {}".format(kind.replace("_", " "), result.kind), _pos(node))
        elif kind == CALCULATION and result.kind not in (NUMBER, TEXT, CHOICE, ANY):
            self.fail("E_EXPR_TYPE", "a calculation must yield a number or text, got {} (use if(..., 1, 0))".format(result.kind), _pos(node))
        elif kind == PIPE and result.kind not in (NUMBER, TEXT, CHOICE, ANY):
            self.fail("E_EXPR_TYPE", "only numbers and text can be piped into text, got {}".format(result.kind), _pos(node))

    def expect(self, node: ast.Node, wanted: str, what: str) -> Type:
        found = self.visit(node)
        if found.kind in (wanted, ANY):
            return found
        if wanted == TEXT and found.kind == CHOICE:
            return found
        return self.fail("E_EXPR_TYPE", "{} needs {}, got {}".format(what, wanted, found.kind), _pos(node))

    def resolve(self, node: ast.Node) -> Optional[Resolved]:
        """把 Ref／self 解析成题目；失败时记下错误并返回 None。"""
        if isinstance(node, ast.SelfRef):
            owner = self._context.owner
            if self._context.kind != VALIDATION or owner is None:
                self.fail("E_EXPR_SELF_NOT_ALLOWED", "'self' is only allowed in a validation rule", node.pos)
                return None
            return self._resolve_question(owner, node.pos)
        if not isinstance(node, ast.Ref):
            return None
        try:
            resolved = self._scope.resolve(node)
        except ResolutionError as error:
            self.fail(error.code, error.message, node.pos)
            return None
        self.references.append((resolved.question, node.pos))
        return resolved

    def _resolve_question(self, question: Question, pos: int) -> Optional[Resolved]:
        try:
            return self._scope.resolve_question(question)
        except ResolutionError as error:
            self.fail(error.code, error.message, pos)
            return None

    # ------------------------------------------------------------- 节点

    def visit(self, node: ast.Node) -> Type:
        visitor = _VISITORS[type(node)]
        return visitor(self, node)

    def _number(self, node: ast.Number) -> Type:
        return TYPE_NUMBER

    def _string(self, node: ast.String) -> Type:
        bad = sorted({char for char in node.value if char in FORBIDDEN_STRING_CHARS or ord(char) < 0x20 or ord(char) == 0x7F})
        if bad:
            return self.fail(
                "E_EXPR_STRING_CHAR", "string literal may not contain {}".format(", ".join(repr(char) for char in bad)), node.pos
            )
        return TYPE_TEXT

    def _bool(self, node: ast.Bool) -> Type:
        return TYPE_BOOL

    def _reference(self, node: ast.Node) -> Type:
        resolved = self.resolve(node)
        return resolved.type if resolved else TYPE_ANY

    def _unary(self, node: ast.Unary) -> Type:
        if node.op == "not":
            self.expect(node.operand, BOOL, "'not'")
            return TYPE_BOOL
        self.expect(node.operand, NUMBER, "unary '-'")
        return TYPE_NUMBER

    def _binary(self, node: ast.Binary) -> Type:
        if node.op in ast.BOOLEAN_OPERATORS:
            self.expect(node.left, BOOL, "'{}'".format(node.op))
            self.expect(node.right, BOOL, "'{}'".format(node.op))
            return TYPE_BOOL
        if node.op in ast.ARITHMETIC_OPERATORS:
            self.expect(node.left, NUMBER, "'{}'".format(node.op))
            self.expect(node.right, NUMBER, "'{}'".format(node.op))
            if node.op == "/" and isinstance(node.right, ast.Number) and float(node.right.text) == 0:
                self.fail("E_EXPR_DIVISION_BY_ZERO", "division by the literal 0", node.right.pos)
            return TYPE_NUMBER
        left = self.visit(node.left)
        right = self.visit(node.right)
        if node.op in ast.EQUALITY_OPERATORS:
            self._check_equality(left, right, node)
        else:
            self._check_ordering(left, right, node)
        return TYPE_BOOL

    def _check_equality(self, left: Type, right: Type, node: ast.Binary) -> None:
        if not _comparable(left, right):
            self.fail("E_EXPR_TYPE", "cannot compare {} with {}".format(left.kind, right.kind), node.pos)
            return
        self._check_domain(left, node.right)
        self._check_domain(right, node.left)

    def _check_ordering(self, left: Type, right: Type, node: ast.Binary) -> None:
        if ANY in (left.kind, right.kind):
            return
        if (left.kind, right.kind) in ((NUMBER, NUMBER), (TEXT, TEXT)):
            return
        self.fail("E_EXPR_TYPE", "'{}' needs two numbers or two texts, got {} and {}".format(node.op, left.kind, right.kind), node.pos)

    def _check_domain(self, choice: Type, other: ast.Node) -> None:
        if choice.kind == CHOICE and isinstance(other, ast.String) and other.value not in choice.domain:
            self.fail(
                "E_EXPR_UNKNOWN_ANSWER_CODE",
                "{!r} is not an answer code here (valid: {})".format(other.value, ", ".join(sorted(choice.domain))),
                other.pos,
            )

    def _in_list(self, node: ast.InList) -> Type:
        if node.container is not None:
            return self._in_set(node)
        item = self.visit(node.item)
        for option in node.options:
            option_type = self.visit(option)
            if not _comparable(item, option_type):
                self.fail("E_EXPR_TYPE", "cannot compare {} with {}".format(item.kind, option_type.kind), _pos(option))
                continue
            self._check_domain(item, option)
        return TYPE_BOOL

    def _in_set(self, node: ast.InList) -> Type:
        resolved = self.resolve(node.container)
        if resolved is None:
            return TYPE_BOOL
        if resolved.shape != WHOLE_SET:
            return self.fail("E_EXPR_TYPE", "'in' needs a list [...] or a multiple-choice question", _pos(node.container))
        if not isinstance(node.item, ast.String):
            return self.fail("E_EXPR_TYPE", "the left side of 'in <multiple choice>' must be a subquestion code string", _pos(node.item))
        if node.item.value not in resolved.type.domain:
            self.fail(
                "E_EXPR_UNKNOWN_SUBQUESTION",
                "question {} has no subquestion {!r}".format(resolved.question.code, node.item.value),
                node.item.pos,
            )
        return TYPE_BOOL

    def _call(self, node: ast.Call) -> Type:
        signature = FUNCTIONS.get(node.name)
        if signature is None:
            return self.fail(
                "E_EXPR_UNKNOWN_FUNCTION",
                "unknown function {!r} (allowed: {})".format(node.name, ", ".join(sorted(FUNCTIONS))),
                node.pos,
            )
        low, high = signature.arity
        if len(node.args) < low or (high is not None and len(node.args) > high):
            return self.fail("E_EXPR_ARITY", "{}() takes {} arguments, got {}".format(node.name, _arity_text(low, high), len(node.args)), node.pos)
        return signature.check(self, node)


def _pos(node: ast.Node) -> int:
    return getattr(node, "pos", 0)


def _comparable(left: Type, right: Type) -> bool:
    if ANY in (left.kind, right.kind):
        return True
    if left.is_textual and right.is_textual:
        return True
    return left.kind == right.kind and left.kind in (NUMBER, BOOL)


def _arity_text(low: int, high: Optional[int]) -> str:
    if high is None:
        return "at least {}".format(low)
    if low == high:
        return str(low)
    return "{} to {}".format(low, high)


_VISITORS: Dict[type, Callable] = {
    ast.Number: _Checker._number,
    ast.String: _Checker._string,
    ast.Bool: _Checker._bool,
    ast.Ref: _Checker._reference,
    ast.SelfRef: _Checker._reference,
    ast.Unary: _Checker._unary,
    ast.Binary: _Checker._binary,
    ast.InList: _Checker._in_list,
    ast.Call: _Checker._call,
}


# ------------------------------------------------------------------ 函数


@dataclass(frozen=True)
class Signature:
    arity: Tuple[int, Optional[int]]
    check: Callable[[_Checker, ast.Call], Type]


def _reference_argument(checker: _Checker, node: ast.Node, function: str) -> Optional[Resolved]:
    if not isinstance(node, (ast.Ref, ast.SelfRef)):
        checker.fail("E_EXPR_TYPE", "{}() needs a question reference".format(function), _pos(node))
        return None
    return checker.resolve(node)


def _check_answered(checker: _Checker, node: ast.Call) -> Type:
    _reference_argument(checker, node.args[0], "answered")
    return TYPE_BOOL


def _check_count(checker: _Checker, node: ast.Call) -> Type:
    for argument in node.args:
        resolved = _reference_argument(checker, argument, "count")
        if resolved is not None and resolved.shape == WHOLE_SET and len(node.args) > 1:
            checker.fail("E_EXPR_ARITY", "count(<multiple choice>) takes exactly one argument", _pos(argument))
    return TYPE_NUMBER


def _check_numbers(checker: _Checker, node: ast.Call) -> Type:
    for argument in node.args:
        checker.expect(argument, NUMBER, "{}()".format(node.name))
    return TYPE_NUMBER


def _check_round(checker: _Checker, node: ast.Call) -> Type:
    checker.expect(node.args[0], NUMBER, "round()")
    if len(node.args) == 2 and not (isinstance(node.args[1], ast.Number) and node.args[1].text.isdigit()):
        checker.fail("E_EXPR_TYPE", "round() precision must be a whole number literal", _pos(node.args[1]))
    return TYPE_NUMBER


def _check_if(checker: _Checker, node: ast.Call) -> Type:
    checker.expect(node.args[0], BOOL, "if() condition")
    first = checker.visit(node.args[1])
    second = checker.visit(node.args[2])
    if not _comparable(first, second):
        return checker.fail("E_EXPR_TYPE", "if() branches must have the same type, got {} and {}".format(first.kind, second.kind), node.pos)
    return _join_types(first, second)


def _check_coalesce(checker: _Checker, node: ast.Call) -> Type:
    resolved = _reference_argument(checker, node.args[0], "coalesce")
    fallback = checker.visit(node.args[1])
    if resolved is None:
        return fallback
    if resolved.shape == WHOLE_SET or not _comparable(resolved.type, fallback):
        return checker.fail("E_EXPR_TYPE", "coalesce() fallback must match the question's type", _pos(node.args[1]))
    return _join_types(resolved.type, fallback)


def _check_length(checker: _Checker, node: ast.Call) -> Type:
    checker.expect(node.args[0], TEXT, "length()")
    return TYPE_NUMBER


def _check_join(checker: _Checker, node: ast.Call) -> Type:
    for argument in node.args:
        found = checker.visit(argument)
        if found.kind not in (TEXT, CHOICE, NUMBER, ANY):
            checker.fail("E_EXPR_TYPE", "join() takes text or numbers, got {}".format(found.kind), _pos(argument))
    return TYPE_TEXT


def _check_label(checker: _Checker, node: ast.Call) -> Type:
    resolved = _reference_argument(checker, node.args[0], "label")
    if resolved is not None and not resolved.is_label_capable:
        checker.fail("E_EXPR_TYPE", "label() needs a single-choice question or an array row", _pos(node.args[0]))
    return TYPE_TEXT


def _join_types(first: Type, second: Type) -> Type:
    if first.kind == ANY:
        return second
    if second.kind == ANY or first.kind == second.kind == NUMBER or first.kind == second.kind == BOOL:
        return first
    return TYPE_TEXT


FUNCTIONS: Dict[str, Signature] = {
    "answered": Signature((1, 1), _check_answered),
    "count": Signature((1, None), _check_count),
    "sum": Signature((1, None), _check_numbers),
    "abs": Signature((1, 1), _check_numbers),
    "floor": Signature((1, 1), _check_numbers),
    "ceil": Signature((1, 1), _check_numbers),
    "round": Signature((1, 2), _check_round),
    "if": Signature((3, 3), _check_if),
    "coalesce": Signature((2, 2), _check_coalesce),
    "length": Signature((1, 1), _check_length),
    "join": Signature((2, None), _check_join),
    "label": Signature((1, 1), _check_label),
}
