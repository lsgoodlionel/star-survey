"""逻辑 DSL 的词法与递归下降解析。

没有 eval，也不靠正则拆表达式：逐字符切词，再按文法逐级下降，任何不认识的
字符或多余的记号都当场报错，错误带源文本里的偏移。文法见
``platform/contracts/survey-logic-dsl-v1.md``。
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import ast

MAX_EXPRESSION_LENGTH = 2000
MAX_DEPTH = 32

KEYWORDS = frozenset({"and", "or", "not", "in", "true", "false", "self"})
_COMPARISON_OPERATORS = ("==", "!=", "<=", ">=", "<", ">")
_TWO_CHAR_OPERATORS = ("==", "!=", "<=", ">=")
_ONE_CHAR_OPERATORS = frozenset("<>+-*/()[],.")
_ESCAPES = {'"': '"', "'": "'", "\\": "\\"}

NUMBER = "number"
STRING = "string"
IDENT = "ident"
OPERATOR = "op"
END = "end"


class ExpressionSyntaxError(ValueError):
    def __init__(self, message: str, position: int):
        super().__init__("{} (column {})".format(message, position + 1))
        self.message = message
        self.position = position


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int


def parse_expression(source: str, offset: int = 0) -> ast.Node:
    """解析一个完整表达式；offset 用于模板里的占位符，让位置保持绝对。"""
    if len(source) > MAX_EXPRESSION_LENGTH:
        raise ExpressionSyntaxError(
            "expression longer than {} characters".format(MAX_EXPRESSION_LENGTH), offset
        )
    parser = _Parser(_tokenize(source, offset))
    if parser.peek().kind == END:
        raise ExpressionSyntaxError("expression is empty", offset)
    node = parser.expression()
    trailing = parser.peek()
    if trailing.kind != END:
        raise ExpressionSyntaxError("unexpected {!r}".format(trailing.text), trailing.pos)
    return node


def parse_template(text: str) -> Tuple[ast.Segment, ...]:
    """把带 ``{{ 表达式 }}`` 占位符的文本拆成原样段与占位符段。"""
    segments: List[ast.Segment] = []
    cursor = 0
    while True:
        start = text.find("{{", cursor)
        if start < 0:
            break
        if start > cursor:
            segments.append(ast.Literal(text[cursor:start]))
        end = _placeholder_end(text, start + 2)
        segments.append(ast.Placeholder(parse_expression(text[start + 2:end], start + 2), start))
        cursor = end + 2
    if cursor < len(text):
        segments.append(ast.Literal(text[cursor:]))
    return tuple(segments)


def _placeholder_end(text: str, index: int) -> int:
    quote = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif text.startswith("}}", index):
            return index
        index += 1
    raise ExpressionSyntaxError("placeholder '{{' is never closed with '}}'", len(text))


# ------------------------------------------------------------------ 词法


def _tokenize(source: str, offset: int) -> List[Token]:
    tokens: List[Token] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char in " \t\r\n":
            index += 1
        elif char.isdigit():
            index = _read_number(source, index, offset, tokens)
        elif char in "\"'":
            index = _read_string(source, index, offset, tokens)
        elif _is_letter(char):
            end = index + 1
            while end < len(source) and (_is_letter(source[end]) or source[end].isdigit()):
                end += 1
            tokens.append(Token(IDENT, source[index:end], index + offset))
            index = end
        elif source[index:index + 2] in _TWO_CHAR_OPERATORS:
            tokens.append(Token(OPERATOR, source[index:index + 2], index + offset))
            index += 2
        elif char in _ONE_CHAR_OPERATORS:
            tokens.append(Token(OPERATOR, char, index + offset))
            index += 1
        else:
            raise ExpressionSyntaxError("unexpected character {!r}".format(char), index + offset)
    tokens.append(Token(END, "end of expression", len(source) + offset))
    return tokens


def _is_letter(char: str) -> bool:
    return ("a" <= char <= "z") or ("A" <= char <= "Z")


def _read_number(source: str, index: int, offset: int, tokens: List[Token]) -> int:
    end = index
    while end < len(source) and source[end].isdigit():
        end += 1
    if end < len(source) and source[end] == "." and end + 1 < len(source) and source[end + 1].isdigit():
        end += 1
        while end < len(source) and source[end].isdigit():
            end += 1
    if end < len(source) and (_is_letter(source[end]) or source[end] == "_"):
        raise ExpressionSyntaxError("a number cannot run into a name", end + offset)
    tokens.append(Token(NUMBER, source[index:end], index + offset))
    return end


def _read_string(source: str, index: int, offset: int, tokens: List[Token]) -> int:
    quote = source[index]
    chars: List[str] = []
    cursor = index + 1
    while cursor < len(source):
        char = source[cursor]
        if char == "\\":
            escaped = source[cursor + 1:cursor + 2]
            if escaped not in _ESCAPES:
                raise ExpressionSyntaxError("unsupported escape in string", cursor + offset)
            chars.append(_ESCAPES[escaped])
            cursor += 2
            continue
        if char == quote:
            tokens.append(Token(STRING, "".join(chars), index + offset))
            return cursor + 1
        chars.append(char)
        cursor += 1
    raise ExpressionSyntaxError("string is never closed", index + offset)


# ------------------------------------------------------------------ 文法


class _Parser:
    def __init__(self, tokens: List[Token]):
        self._tokens = tokens
        self._index = 0
        self._depth = 0

    def peek(self) -> Token:
        return self._tokens[self._index]

    def _next(self) -> Token:
        token = self._tokens[self._index]
        if token.kind != END:
            self._index += 1
        return token

    def _accept(self, text: str) -> Optional[Token]:
        token = self.peek()
        if token.kind in (OPERATOR, IDENT) and token.text == text:
            return self._next()
        return None

    def _expect(self, text: str) -> Token:
        token = self._accept(text)
        if token is None:
            found = self.peek()
            raise ExpressionSyntaxError("expected {!r}, found {!r}".format(text, found.text), found.pos)
        return token

    def _enter(self, pos: int) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise ExpressionSyntaxError("expression nests deeper than {}".format(MAX_DEPTH), pos)

    def expression(self) -> ast.Node:
        self._enter(self.peek().pos)
        try:
            return self._or()
        finally:
            self._depth -= 1

    def _or(self) -> ast.Node:
        node = self._and()
        while True:
            token = self._accept("or")
            if token is None:
                return node
            node = ast.Binary("or", node, self._and(), token.pos)

    def _and(self) -> ast.Node:
        node = self._not()
        while True:
            token = self._accept("and")
            if token is None:
                return node
            node = ast.Binary("and", node, self._not(), token.pos)

    def _not(self) -> ast.Node:
        token = self._accept("not")
        if token is None:
            return self._comparison()
        self._enter(token.pos)
        try:
            return ast.Unary("not", self._not(), token.pos)
        finally:
            self._depth -= 1

    def _comparison(self) -> ast.Node:
        left = self._additive()
        token = self.peek()
        if token.kind == OPERATOR and token.text in _COMPARISON_OPERATORS:
            self._next()
            node = ast.Binary(token.text, left, self._additive(), token.pos)
        elif token.kind == IDENT and token.text == "in":
            self._next()
            node = self._membership(left, token.pos)
        else:
            return left
        after = self.peek()
        if (after.kind == OPERATOR and after.text in _COMPARISON_OPERATORS) or (
            after.kind == IDENT and after.text == "in"
        ):
            raise ExpressionSyntaxError("comparisons cannot be chained; use 'and'", after.pos)
        return node

    def _membership(self, item: ast.Node, pos: int) -> ast.Node:
        if self._accept("[") is None:
            return ast.InList(item, (), self._additive(), pos)
        options = [self._additive()]
        while self._accept(","):
            options.append(self._additive())
        self._expect("]")
        return ast.InList(item, tuple(options), None, pos)

    def _additive(self) -> ast.Node:
        node = self._multiplicative()
        while True:
            token = self._accept("+") or self._accept("-")
            if token is None:
                return node
            node = ast.Binary(token.text, node, self._multiplicative(), token.pos)

    def _multiplicative(self) -> ast.Node:
        node = self._unary()
        while True:
            token = self._accept("*") or self._accept("/")
            if token is None:
                return node
            node = ast.Binary(token.text, node, self._unary(), token.pos)

    def _unary(self) -> ast.Node:
        token = self._accept("-")
        if token is None:
            return self._primary()
        self._enter(token.pos)
        try:
            return ast.Unary("-", self._unary(), token.pos)
        finally:
            self._depth -= 1

    def _primary(self) -> ast.Node:
        token = self._next()
        if token.kind == NUMBER:
            return ast.Number(token.text, token.pos)
        if token.kind == STRING:
            return ast.String(token.text, token.pos)
        if token.kind == OPERATOR and token.text == "(":
            node = self.expression()
            self._expect(")")
            return node
        if token.kind == IDENT:
            return self._word(token)
        raise ExpressionSyntaxError("expected a value, found {!r}".format(token.text), token.pos)

    def _word(self, token: Token) -> ast.Node:
        if token.text in ("true", "false"):
            return ast.Bool(token.text == "true", token.pos)
        if token.text == "self":
            return ast.SelfRef(token.pos)
        if token.text in KEYWORDS:
            raise ExpressionSyntaxError("unexpected keyword {!r}".format(token.text), token.pos)
        if self.peek().kind == OPERATOR and self.peek().text == "(":
            if token.text == "q":
                return self._uuid_reference(token)
            return self._call(token)
        return self._member(token.text, False, token.pos)

    def _uuid_reference(self, token: Token) -> ast.Node:
        self._expect("(")
        target = self._next()
        if target.kind != STRING or not target.text:
            raise ExpressionSyntaxError("q() takes one question or subquestion UUID string", target.pos)
        self._expect(")")
        return self._member(target.text, True, token.pos)

    def _member(self, target: str, by_uuid: bool, pos: int) -> ast.Node:
        member = None
        scale = None
        if self._accept("."):
            name = self._next()
            if name.kind != IDENT:
                raise ExpressionSyntaxError("expected a subquestion code after '.'", name.pos)
            member = name.text
        if self._accept("["):
            index = self._next()
            if index.kind != NUMBER or not index.text.isdigit():
                raise ExpressionSyntaxError("expected a scale number inside [ ]", index.pos)
            self._expect("]")
            scale = int(index.text)
        return ast.Ref(target, by_uuid, member, scale, pos)

    def _call(self, token: Token) -> ast.Node:
        self._enter(token.pos)
        try:
            self._expect("(")
            args: List[ast.Node] = []
            if self._accept(")") is None:
                args.append(self.expression())
                while self._accept(","):
                    args.append(self.expression())
                self._expect(")")
            return ast.Call(token.text, tuple(args), token.pos)
        finally:
            self._depth -= 1
