"""输入格式 → ExpressionScript 服务端规则。

规则进题目属性 ``em_validation_q``，由引擎在服务端 ``_ValidateQuestion`` 里重算
（相关性同样在服务端重算，不信任表单里的 ``relevance<qid>`` 等影子字段），所以
浏览器端的任何篡改都绕不过去。两条写法约束：

1. 正则里不出现 ``{`` ``}`` ``\\``：自由文本的 ``.NAOK`` 是实体编码后的值、模式又被放进
   EM 的双引号字符串（ADR 0006 限制 2），因此重复次数一律展开成字符组。
2. 校验码用加权求和再取模，模运算写成 ``s - floor(s / m) * m``（EM 没有 ``%``）。

身份证：GB 11643-1999，前 17 位加权（7 9 10 5 8 4 2 1 6 3 7 9 10 5 8 4 2）模 11 查表
``10X98765432``，另用 ``checkdate()`` 校验出生日期。统一社会信用代码：GB 32100-2015，
字符集 ``0-9A-HJ-NPQRTUWXY``（31 个），前 17 位加权模 31，校验位 ``(31 - s mod 31) mod 31``。
"""

from dataclasses import dataclass
from typing import Callable, Dict, Sequence

_DIGIT = "[0-9]"
_USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_CLASS = "[0-9A-HJ-NPQRTUWXY]"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_TABLE = "10X98765432"


@dataclass(frozen=True)
class FormatRule:
    name: str
    tip: str
    build: Callable[[str], str]

    def expression(self, variable: str) -> str:
        """``variable`` 是 ``CODE.NAOK`` 形式的引擎变量；未作答时放行（必答另由 mandatory 管）。"""
        return "(is_empty({v}) or {rule})".format(v=variable, rule=self.build(variable))


def _regex(pattern: str, variable: str) -> str:
    """形状检查作用在解码后的原文上（``.NAOK`` 是实体编码过的）。校验码部分只在形状通过、
    即全是字母数字时才有意义，那时编码前后相同，不必再解码。"""
    return 'regexMatch("/^{}$/", html_entity_decode({}))'.format(pattern, variable)


def _modulo(expression: str, divisor: int) -> str:
    return "({e} - floor({e} / {d}) * {d})".format(e=expression, d=divisor)


def _weighted_sum(variable: str, weights: Sequence[int], digit: Callable[[str], str]) -> str:
    terms = ", ".join(
        "{} * {}".format(digit("substr({}, {}, 1)".format(variable, index)), weight)
        for index, weight in enumerate(weights)
    )
    return "sum({})".format(terms)


def _mobile(variable: str) -> str:
    return _regex("1[3-9]" + _DIGIT * 9, variable)


def _postcode(variable: str) -> str:
    return _regex(_DIGIT * 6, variable)


def _email(variable: str) -> str:
    return _regex("[^@ ]+@[^@ ]+[.][^@ ]+", variable)


def _id_card(variable: str) -> str:
    shape = _regex("[1-9]" + _DIGIT * 16 + "[0-9Xx]", variable)
    birth = "checkdate(intval(substr({v}, 10, 2)), intval(substr({v}, 12, 2)), intval(substr({v}, 6, 4)))".format(
        v=variable
    )
    total = _weighted_sum(variable, _ID_WEIGHTS, lambda char: "intval({})".format(char))
    check = 'substr("{}", {}, 1) == strtoupper(substr({}, 17, 1))'.format(
        _ID_CHECK_TABLE, _modulo(total, 11), variable
    )
    return "({} and {} and {})".format(shape, birth, check)


def _uscc(variable: str) -> str:
    shape = _regex(_USCC_CLASS * 18, variable)
    total = _weighted_sum(variable, _USCC_WEIGHTS, lambda char: 'strpos("{}", {})'.format(_USCC_ALPHABET, char))
    remainder = _modulo(total, 31)
    check = 'substr("{a}", if({r} == 0, 0, 31 - {r}), 1) == substr({v}, 17, 1)'.format(
        a=_USCC_ALPHABET, r=remainder, v=variable
    )
    return "({} and {})".format(shape, check)


FORMATS: Dict[str, FormatRule] = {
    rule.name: rule
    for rule in (
        FormatRule("email", "请输入有效的邮箱地址", _email),
        FormatRule("cn_mobile", "请输入 11 位中国大陆手机号", _mobile),
        FormatRule("cn_postcode", "请输入 6 位邮政编码", _postcode),
        FormatRule("cn_id_card", "请输入有效的 18 位居民身份证号", _id_card),
        FormatRule("cn_uscc", "请输入有效的 18 位统一社会信用代码", _uscc),
    )
}

#: 能带 format 的题型：只有一列自由文本。
FORMAT_TYPES = frozenset({"S"})
