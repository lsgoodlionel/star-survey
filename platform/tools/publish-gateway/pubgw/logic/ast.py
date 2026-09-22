"""逻辑 DSL 的语法树。全部是不可变值对象，pos 是在源文本里的 0 起偏移。"""

from dataclasses import dataclass
from typing import Optional, Tuple, Union


@dataclass(frozen=True)
class Number:
    text: str
    pos: int


@dataclass(frozen=True)
class String:
    value: str
    pos: int


@dataclass(frozen=True)
class Bool:
    value: bool
    pos: int


@dataclass(frozen=True)
class Ref:
    """对一道题（或它的子题／尺度）的引用。target 是题目代码或 UUID。"""

    target: str
    by_uuid: bool
    member: Optional[str]
    scale: Optional[int]
    pos: int


@dataclass(frozen=True)
class SelfRef:
    """校验规则里的 self：规则所属的那道题。"""

    pos: int


@dataclass(frozen=True)
class Unary:
    op: str  # "not" | "-"
    operand: "Node"
    pos: int


@dataclass(frozen=True)
class Binary:
    op: str  # or and == != < <= > >= + - * /
    left: "Node"
    right: "Node"
    pos: int


@dataclass(frozen=True)
class InList:
    """``x in [a, b]`` 或 ``"SQ001" in QMULTI``（此时 options 为空、container 非空）。"""

    item: "Node"
    options: Tuple["Node", ...]
    container: Optional["Node"]
    pos: int


@dataclass(frozen=True)
class Call:
    name: str
    args: Tuple["Node", ...]
    pos: int


Node = Union[Number, String, Bool, Ref, SelfRef, Unary, Binary, InList, Call]


@dataclass(frozen=True)
class Literal:
    """模板里的原样文本。"""

    text: str


@dataclass(frozen=True)
class Placeholder:
    """模板里的 ``{{ 表达式 }}``；pos 是 ``{{`` 的位置。"""

    expression: Node
    pos: int


Segment = Union[Literal, Placeholder]

ARITHMETIC_OPERATORS = frozenset({"+", "-", "*", "/"})
EQUALITY_OPERATORS = frozenset({"==", "!="})
ORDERING_OPERATORS = frozenset({"<", "<=", ">", ">="})
BOOLEAN_OPERATORS = frozenset({"and", "or"})
