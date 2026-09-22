"""语法树 → ExpressionScript 受限子集。只处理已经通过类型检查的树。

产出只用到下面这些东西（其余引擎语法一概不生成）：

- 变量：``<qcode>.NAOK``（被条件隐藏的题读成空串，而不是让整个表达式失效）
  与 ``<qcode>.shown``（仅 label()）；
- 运算符：``and or ! == != < <= > >= - * /``，每个二元运算都加括号；
- 函数：``if is_empty count sum abs floor ceil round strlen join``；
- 双引号字符串，只转义 ``"``（字面量里的 ``\\ { } < > &`` 已被类型检查拒绝）。

算术的空值语义：任何一个操作数未作答（或被隐藏）结果就是空；除数为 0 结果也是空。
引擎里 ``"" + 5`` 是字符串拼接、``x / 0`` 在 PHP 与 JS 两侧结果不同，所以整棵算术
子树外面包一层 ``if(<守卫>, "", <算式>)``，加法改用 ``sum()``。
"""

from typing import Callable, List

from . import ast
from .scope import SELECTED, WHOLE_SET, Resolved, Scope
from ..model import Question

_ARITHMETIC_FUNCTIONS = frozenset({"abs", "floor", "ceil", "round"})
_NEVER_EMPTY_FUNCTIONS = frozenset({"count", "sum", "length", "answered"})


class Emitter:
    def __init__(self, scope: Scope, owner: Question = None):
        self._scope = scope
        self._owner = owner

    def emit(self, node: ast.Node) -> str:
        if _is_arithmetic(node):
            guards: List[str] = []
            body = self._arithmetic(node, guards)
            if not guards:
                return body
            return 'if({}, "", {})'.format(" or ".join(_unique(guards)), body)
        return _EMITTERS[type(node)](self, node)

    def resolve(self, node: ast.Node) -> Resolved:
        if isinstance(node, ast.SelfRef):
            return self._scope.resolve_question(self._owner)
        return self._scope.resolve(node)

    # ------------------------------------------------------------- 算术

    def _arithmetic(self, node: ast.Node, guards: List[str]) -> str:
        if isinstance(node, ast.Binary):
            left = self._arithmetic(node.left, guards)
            right = self._arithmetic(node.right, guards)
            if node.op == "+":
                return "sum({}, {})".format(left, right)
            if node.op == "/" and not isinstance(node.right, ast.Number):
                guards.append("({}) == 0".format(right))
            return "({} {} {})".format(left, node.op, right)
        if isinstance(node, ast.Unary):
            return "(-{})".format(self._arithmetic(node.operand, guards))
        if isinstance(node, ast.Call) and node.name in _ARITHMETIC_FUNCTIONS:
            inner = self._arithmetic(node.args[0], guards)
            if node.name == "round" and len(node.args) == 2:
                return "round({}, {})".format(inner, node.args[1].text)
            return "{}({})".format(node.name, inner)
        text = self.emit(node)
        if _may_be_empty(node):
            guards.append("is_empty({})".format(text))
        return text

    # ------------------------------------------------------------- 节点

    def _number(self, node: ast.Number) -> str:
        return node.text

    def _string(self, node: ast.String) -> str:
        return '"{}"'.format(node.value.replace('"', '\\"'))

    def _bool(self, node: ast.Bool) -> str:
        return "1" if node.value else "0"

    def _reference(self, node: ast.Node) -> str:
        resolved = self.resolve(node)
        if resolved.shape == SELECTED:
            return '({}.NAOK == "Y")'.format(resolved.variable)
        return "{}.NAOK".format(resolved.variable)

    def _unary(self, node: ast.Unary) -> str:
        return "(!{})".format(self.emit(node.operand))

    def _binary(self, node: ast.Binary) -> str:
        return "({} {} {})".format(self.emit(node.left), node.op, self.emit(node.right))

    def _in_list(self, node: ast.InList) -> str:
        if node.container is not None:
            resolved = self.resolve(node.container)
            return '({}_{}.NAOK == "Y")'.format(resolved.question.code, node.item.value)
        item = self.emit(node.item)
        tests = ["({} == {})".format(item, self.emit(option)) for option in node.options]
        if len(tests) == 1:
            return tests[0]
        return "({})".format(" or ".join(tests))

    def _call(self, node: ast.Call) -> str:
        return _CALLS[node.name](self, node)

    # ------------------------------------------------------------- 函数

    def _answered(self, node: ast.Call) -> str:
        resolved = self.resolve(node.args[0])
        if resolved.shape == WHOLE_SET:
            return "({} > 0)".format(_count(resolved.members))
        if resolved.shape == SELECTED:
            return self._reference(node.args[0])
        return "(!is_empty({}.NAOK))".format(resolved.variable)

    def _count(self, node: ast.Call) -> str:
        first = self.resolve(node.args[0])
        if first.shape == WHOLE_SET:
            return _count(first.members)
        return _count(self.resolve(argument).variable for argument in node.args)

    def _coalesce(self, node: ast.Call) -> str:
        value = self.emit(node.args[0])
        return "if(is_empty({}), {}, {})".format(value, self.emit(node.args[1]), value)

    def _label(self, node: ast.Call) -> str:
        return "{}.shown".format(self.resolve(node.args[0]).variable)


def _count(variables) -> str:
    return "count({})".format(", ".join("{}.NAOK".format(variable) for variable in variables))


def _is_arithmetic(node: ast.Node) -> bool:
    if isinstance(node, ast.Binary):
        return node.op in ast.ARITHMETIC_OPERATORS
    if isinstance(node, ast.Unary):
        return node.op == "-"
    return isinstance(node, ast.Call) and node.name in _ARITHMETIC_FUNCTIONS


def _may_be_empty(node: ast.Node) -> bool:
    if isinstance(node, ast.Number):
        return False
    if isinstance(node, ast.Call):
        if node.name in _NEVER_EMPTY_FUNCTIONS:
            return False
        if node.name == "coalesce":
            return _may_be_empty(node.args[1])
    return True


def _unique(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _plain_call(name: str) -> Callable[[Emitter, ast.Call], str]:
    return lambda emitter, node: "{}({})".format(name, ", ".join(emitter.emit(arg) for arg in node.args))


_EMITTERS = {
    ast.Number: Emitter._number,
    ast.String: Emitter._string,
    ast.Bool: Emitter._bool,
    ast.Ref: Emitter._reference,
    ast.SelfRef: Emitter._reference,
    ast.Unary: Emitter._unary,
    ast.Binary: Emitter._binary,
    ast.InList: Emitter._in_list,
    ast.Call: Emitter._call,
}

_CALLS = {
    "answered": Emitter._answered,
    "count": Emitter._count,
    "sum": _plain_call("sum"),
    "if": _plain_call("if"),
    "join": _plain_call("join"),
    "length": _plain_call("strlen"),
    "coalesce": Emitter._coalesce,
    "label": Emitter._label,
}
