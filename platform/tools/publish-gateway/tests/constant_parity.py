"""从三端源码里把**数值上限常量**读出来，并按声明的规则比一遍。

这是机制，对照表在 ``test_limit_parity.py``。分开放是为了让机制本身可以被喂
**人为制造的不一致**：一个永远不会红的一致性检查毫无价值，所以每个抽取器与
比对规则都必须有「它确实会红」的用例（见 ``LimitParityGoesRedTest``）。

三端各自怎么读：

* 网关（Python）——直接 import 取值。网关里有 ``MAX_PK_PAIRS = MAX_COLUMNS // 2``
  这类算出来的常量，按源码文本抓会漏掉真实取值；
* 插件（PHP）与平台（Java）——按源码文本抓。这份用例跟着网关单测跑，
  不装 PHP、不起 JVM，所以只能读文本。

文本抽取的两条底线：

1. 常量**找不到**必须抛 :class:`ConstantNotFound`，绝不能当成「这一端没有这个限制」
   静默跳过——改名、挪走、删掉都要立刻转红；
2. 只认 ``static final int/long``（Java）与 ``const``（PHP）这类**编译期常量**的字面量
   与简单算式（``1024 * 1024 - 4096``）。读不懂就抛，不猜。
"""

import ast
import importlib
import operator
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = REPO_ROOT / "plugins/MjyQuestionExtensions"
JAVA_ROOT = REPO_ROOT / "platform/services/business/src/main/java/cn/mjy/platform"

#: 读一个源码文件；比对函数按这个签名接收 reader，测试可以塞一个改过字节的假 reader。
Reader = Callable[[Path], str]


class ConstantNotFound(AssertionError):
    """常量不在源码里：改名、挪走或删掉了。必须转红，不能当成「这一端没有它」。"""


class ConstantNotLiteral(AssertionError):
    """常量在，但取值不是字面量或简单算式，这份文本抽取读不懂它。"""


def read_source(path: Path) -> str:
    if not path.exists():
        raise ConstantNotFound("源码文件不存在：{}".format(path))
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 取值求解

_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.FloorDiv: operator.floordiv,
    ast.LShift: operator.lshift,
}


def _normalise_number_tokens(expression: str) -> str:
    """把 Java/PHP 的数字写法磨成 Python 认得的：``1_048_576L`` → ``1048576``。"""
    return re.sub(
        r"(?<![\w.])(\d[\d_]*)[lL]?(?![\w.])",
        lambda match: match.group(1).replace("_", ""),
        expression,
    ).strip()


def _fold(node: ast.AST, where: str) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_fold(node.operand, where)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        return _BINARY_OPS[type(node.op)](_fold(node.left, where), _fold(node.right, where))
    raise ConstantNotLiteral("{} 的取值不是整数字面量或简单算式".format(where))


def evaluate_int(expression: str, where: str) -> int:
    """把源码里的整型常量表达式算成 int；读不懂就抛，绝不返回一个猜出来的数。"""
    text = _normalise_number_tokens(expression)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise ConstantNotLiteral("{} 的取值 {!r} 解析不了".format(where, expression))
    return _fold(tree.body, where)


# ------------------------------------------------------------------ 三端抽取


@dataclass(frozen=True)
class PhpConstant:
    """插件里的 ``const NAME = …;``（public/protected/private 都算）。"""

    file: str
    name: str

    @property
    def path(self) -> Path:
        return PLUGIN_ROOT / self.file

    @property
    def where(self) -> str:
        return "插件 {}::{}".format(self.file, self.name)

    def value(self, reader: Reader = read_source) -> int:
        pattern = r"(?:public|protected|private)?\s*const\s+{}\s*=\s*([^;]+);".format(re.escape(self.name))
        found = re.search(pattern, reader(self.path))
        if not found:
            raise ConstantNotFound("{} 找不到".format(self.where))
        return evaluate_int(found.group(1), self.where)


@dataclass(frozen=True)
class JavaConstant:
    """平台里的 ``static final int|long NAME = …;``（可见性不限）。"""

    file: str
    name: str

    @property
    def path(self) -> Path:
        return JAVA_ROOT / self.file

    @property
    def where(self) -> str:
        return "平台 {}.{}".format(self.file, self.name)

    def value(self, reader: Reader = read_source) -> int:
        pattern = r"static\s+final\s+(?:int|long)\s+{}\s*=\s*([^;]+);".format(re.escape(self.name))
        found = re.search(pattern, reader(self.path))
        if not found:
            raise ConstantNotFound("{} 找不到".format(self.where))
        return evaluate_int(found.group(1), self.where)


@dataclass(frozen=True)
class GatewayConstant:
    """网关自己的模块级常量；import 取值，算出来的常量也读得到。"""

    module: str
    name: str

    @property
    def where(self) -> str:
        return "网关 {}.{}".format(self.module, self.name)

    def value(self, reader: Reader = read_source) -> int:
        module = importlib.import_module(self.module)
        if not hasattr(module, self.name):
            raise ConstantNotFound("{} 找不到".format(self.where))
        value = getattr(module, self.name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConstantNotLiteral("{} 不是整数".format(self.where))
        return value


Side = object  # PhpConstant | JavaConstant | GatewayConstant（三者只需要 where 与 value）

#: 比对规则：所有端取值相同。
SAME = "same"
#: 比对规则：按声明顺序严格递减（外层信封 > 内层预算这种「留余量」关系）。
DECREASING = "decreasing"


@dataclass(frozen=True)
class LimitGroup:
    """一个上限在三端的全部落点，加上它们之间该成立的关系。"""

    name: str
    why: str
    sides: Tuple[Side, ...]
    rule: str = SAME

    def __post_init__(self) -> None:
        if len(self.sides) < 2:
            raise ValueError("{}：一端的常量谈不上一致性，至少要两端".format(self.name))


def readings(group: LimitGroup, reader: Reader = read_source) -> List[Tuple[str, int]]:
    """按声明顺序把每一端读成 ``(出处, 取值)``；任一端读不出来就抛。"""
    return [(side.where, side.value(reader)) for side in group.sides]


def mismatches(group: LimitGroup, reader: Reader = read_source) -> List[str]:
    """不一致的人话描述；一致时返回空列表。抽取失败不在这里吞掉，交给调用方转红。"""
    seen = readings(group, reader)
    if group.rule == SAME:
        values = {value for _, value in seen}
        if len(values) == 1:
            return []
        return ["{}：三端不一致 —— {}".format(group.name, _render(seen))]
    if group.rule == DECREASING:
        problems = []
        for (left_where, left), (right_where, right) in zip(seen, seen[1:]):
            if not left > right:
                problems.append(
                    "{}：{}（{}）必须严格大于 {}（{}）".format(
                        group.name, left_where, left, right_where, right
                    )
                )
        return problems
    raise ValueError("未知的比对规则 {!r}".format(group.rule))


def _render(seen: Sequence[Tuple[str, int]]) -> str:
    return "；".join("{} = {}".format(where, value) for where, value in seen)


def duplicate_names(groups: Sequence[LimitGroup]) -> List[str]:
    """同名分组会让报错指不清是哪一条，对照表里不许出现。"""
    counts: Dict[str, int] = {}
    for group in groups:
        counts[group.name] = counts.get(group.name, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)
