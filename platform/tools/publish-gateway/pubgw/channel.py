"""网关 → 引擎插件的鉴权通道客户端（契约 platform/contracts/plugin-channel-v1.md，ADR 0018）。

副表作答（C 档题型的行×列数据）不在引擎原生答卷表里，RemoteControl 够不着，
而 ``remotecontrol_handle.php`` 全文只派发一个插件事件，加不了插件 RPC 方法。
因此走插件自己开的 ``newDirectRequest`` 端点，并用每实例派生的通道密钥验签：

    GET <engine>/index.php/plugins/direct?plugin=…&function=extensionAnswers&…&sig=…

    签名串 = "<ts>" + "." + <规范化查询串>
    sig    = hex(HMAC-SHA256(通道密钥的 ASCII 字节, 签名串))

签名覆盖**整个**规范化查询串（含 ``plugin`` / ``function`` / ``sid`` / ``responseIds``），
所以签好的请求改投不到别的插件函数，也改投不到别人的答卷。

作答是个人数据：本模块的日志与异常只带实例、sid、条数与失败类型，
**从不带值、不带密钥、不带含 ``sig`` 的完整 URL**。
"""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

PLUGIN_NAME = "MjyQuestionExtensions"
CHANNEL_FUNCTION = "extensionAnswers"
DIRECT_PATH = "/plugins/direct"
#: 派生上下文；将来换算法时升版本号，新旧密钥互不相同。
DERIVATION_PREFIX = "mjy-plugin-channel/v1/"
MIN_INSTANCE_SECRET_BYTES = 32
MAX_RESPONSE_IDS = 200
MAX_QUESTION_CODES = 50
_TIMEOUT_SECONDS = 30
_RPC_SUFFIX = "/admin/remotecontrol"

_QUESTION_CODE = re.compile(r"\A[A-Za-z0-9_]{1,64}\Z")
_GENERATION = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\Z")

Fetch = Callable[[str], bytes]


class ChannelError(RuntimeError):
    """插件不可达，或应答不是本契约的形状。调用方据此整页失败关闭。"""


class InvalidChannelRequest(ValueError):
    """请求本身越界，在发出之前就挡住。"""


@dataclass(frozen=True)
class ExtensionAnswer:
    """一条答卷的一道扩展题。``rows`` 下标即行序，单元格值一律是字符串。"""

    structure_version: str
    is_valid: bool
    rows: Tuple[Mapping[str, str], ...]


def derive_channel_secret(instance_secret: str, engine_instance_id: str) -> str:
    """通道密钥 = hex(HMAC-SHA256(实例密钥的 ASCII 字节, 前缀 + 实例标识))。

    单向：拿到通道密钥推不出实例密钥，因此网关被攻破也伪造不了引擎事件。
    """
    key = (instance_secret or "").encode("utf-8")
    if len(key) < MIN_INSTANCE_SECRET_BYTES:
        raise ValueError("instance secret must be at least {} bytes".format(MIN_INSTANCE_SECRET_BYTES))
    context = (DERIVATION_PREFIX + engine_instance_id).encode("utf-8")
    return hmac.new(key, context, hashlib.sha256).hexdigest()


def canonical_query(params: Mapping[str, str]) -> str:
    """规范化查询串：按参数名（同名再按值）升序，键值各自 RFC 3986 编码，``&`` 连接。

    PHP 的 ``rawurlencode`` 与这里的 ``quote(safe="")`` 对 RFC 3986 的定义一致，
    两端因此算得出同一个串。
    """
    pairs = sorted((str(key), str(value)) for key, value in params.items())
    return "&".join("{}={}".format(quote(key, safe=""), quote(value, safe="")) for key, value in pairs)


def sign(secret: str, timestamp: str, canonical: str) -> str:
    """密钥取十六进制串的 ASCII 字节——与 PHP ``hash_hmac`` 以字符串作密钥一致。"""
    message = (timestamp + "." + canonical).encode("utf-8")
    return hmac.new(secret.encode("ascii"), message, hashlib.sha256).hexdigest()


def _check_response_ids(response_ids: Sequence[int]) -> Tuple[int, ...]:
    ids = tuple(int(value) for value in response_ids)
    if not 1 <= len(ids) <= MAX_RESPONSE_IDS:
        raise InvalidChannelRequest("response ids must be 1..{}".format(MAX_RESPONSE_IDS))
    if len(set(ids)) != len(ids):
        raise InvalidChannelRequest("response ids must be distinct")
    if any(value <= 0 for value in ids):
        raise InvalidChannelRequest("response ids must be positive")
    # 严格升序让签名串规范：同一组答卷号只有一种写法。
    return tuple(sorted(ids))


def _check_question_codes(question_codes: Sequence[str]) -> Tuple[str, ...]:
    codes = tuple(str(code) for code in question_codes)
    if not 1 <= len(codes) <= MAX_QUESTION_CODES:
        raise InvalidChannelRequest("question codes must be 1..{}".format(MAX_QUESTION_CODES))
    if len(set(codes)) != len(codes):
        raise InvalidChannelRequest("question codes must be distinct")
    if any(not _QUESTION_CODE.match(code) for code in codes):
        raise InvalidChannelRequest("question code is outside the grammar")
    return codes


def _check_generation(generation: str) -> str:
    if not _GENERATION.match(str(generation)):
        raise InvalidChannelRequest("generation is outside the grammar")
    return str(generation)


class ExtensionAnswerClient:
    """一个引擎实例的通道客户端。``fetch`` 是测试用的注入点（与 policy/probe.py 同形）。"""

    def __init__(
        self,
        index_url: str,
        engine_instance_id: str,
        secret: str,
        fetch: Optional[Fetch] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self._index_url = index_url.rstrip("/")
        self._instance_id = engine_instance_id
        self._secret = secret
        self._fetch = fetch or _urlopen
        if now is None:
            import time

            now = time.time
        self._now = now

    @classmethod
    def from_rpc_url(
        cls,
        rpc_url: str,
        engine_instance_id: str,
        secret: str,
        fetch: Optional[Fetch] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> Optional["ExtensionAnswerClient"]:
        """从引擎配置里的 RemoteControl 地址推出 index 地址；形状不认识就返回 None。"""
        trimmed = rpc_url.rstrip("/")
        if not trimmed.endswith(_RPC_SUFFIX):
            return None
        return cls(trimmed[: -len(_RPC_SUFFIX)], engine_instance_id, secret, fetch=fetch, now=now)

    def read(
        self,
        survey_id: int,
        generation: str,
        response_ids: Sequence[int],
        question_codes: Sequence[str],
    ) -> Dict[int, Dict[str, ExtensionAnswer]]:
        """读一页扩展题作答。sid 不存在或代次不匹配时返回空字典（正常的空，不是错误）。"""
        ids = _check_response_ids(response_ids)
        codes = _check_question_codes(question_codes)
        params = {
            "plugin": PLUGIN_NAME,
            "function": CHANNEL_FUNCTION,
            "sid": str(int(survey_id)),
            "generation": _check_generation(generation),
            "responseIds": ",".join(str(value) for value in ids),
            "questionCodes": ",".join(codes),
            "ts": str(int(self._now())),
        }
        canonical = canonical_query(params)
        signature = sign(self._secret, params["ts"], canonical)
        url = "{}{}?{}&sig={}".format(self._index_url, DIRECT_PATH, canonical, signature)
        return _parse(self._read_bytes(url), frozenset(ids), frozenset(codes))

    def _read_bytes(self, url: str) -> bytes:
        try:
            return self._fetch(url)
        except ChannelError:
            raise
        except Exception as error:
            # 刻意不带 str(error)：它可能含完整 URL（内有 sig）。类名足够区分失败类型。
            raise ChannelError("extension answer channel unreachable at {} ({})".format(
                self._index_url, type(error).__name__)) from None


def _parse(
    body: bytes,
    wanted_ids: frozenset,
    wanted_codes: frozenset,
) -> Dict[int, Dict[str, ExtensionAnswer]]:
    """严格按契约解析。引擎的应答是外部数据，形状不对就拒绝，绝不猜。"""
    document = _document(body)
    if "error" in document:
        # 401 的体绝不能被当成「没有数据」——那会让一次密钥配错变成一次静默的数据缺失。
        raise ChannelError("extension answer channel refused the read")
    answers = document.get("answers")
    if not isinstance(answers, dict):
        raise ChannelError("extension answer channel returned no answers object")
    parsed = {}
    for raw_id, questions in answers.items():
        response_id = _response_id(raw_id, wanted_ids)
        if not isinstance(questions, dict):
            raise ChannelError("extension answers for a response are not an object")
        parsed[response_id] = {
            _question_code(code, wanted_codes): _answer(value) for code, value in questions.items()
        }
    return parsed


def _document(body: bytes) -> Mapping[str, Any]:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ChannelError("extension answer channel returned a body that is not JSON") from None
    if not isinstance(document, dict):
        raise ChannelError("extension answer channel returned a body that is not an object")
    return document


def _response_id(raw: Any, wanted: frozenset) -> int:
    try:
        response_id = int(str(raw))
    except ValueError:
        raise ChannelError("extension answers are keyed by something that is not a response id") from None
    if response_id not in wanted:
        raise ChannelError("extension answers name a response that was not asked for")
    return response_id


def _question_code(raw: Any, wanted: frozenset) -> str:
    if not isinstance(raw, str) or raw not in wanted:
        raise ChannelError("extension answers name a question that was not asked for")
    return raw


def _answer(raw: Any) -> ExtensionAnswer:
    if not isinstance(raw, dict):
        raise ChannelError("an extension answer is not an object")
    version = raw.get("structureVersion")
    valid = raw.get("isValid")
    rows = raw.get("rows")
    if not isinstance(version, str) or not version:
        raise ChannelError("an extension answer has no structure version")
    if not isinstance(valid, bool):
        raise ChannelError("an extension answer has no validity flag")
    if not isinstance(rows, list):
        raise ChannelError("extension answer rows are not a list")
    return ExtensionAnswer(version, valid, tuple(_row(row) for row in rows))


def _row(raw: Any) -> Mapping[str, str]:
    if not isinstance(raw, dict):
        raise ChannelError("an extension answer row is not an object")
    for key, value in raw.items():
        # 错误信息里不带键名也不带值：列代码与单元格都是作答内容。
        if not isinstance(key, str) or not isinstance(value, str):
            raise ChannelError("an extension answer cell is not a string")
    return dict(raw)


def _urlopen(url: str) -> bytes:
    from http.client import HTTPException
    from urllib.request import Request, urlopen

    try:
        with urlopen(Request(url, method="GET"), timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except (OSError, HTTPException) as error:
        raise ChannelError("extension answer channel transport failed ({})".format(
            type(error).__name__)) from None
