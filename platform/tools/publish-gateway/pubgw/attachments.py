"""``POST /v1/responses/attachment``：取一份上传附件的字节（契约 response-read-v1「附件取件」）。

平台按导出的附件清单逐份来取，网关经插件通道（ADR 0018）向引擎要，两段都是**流式**的：
任何时刻只持有一个 64 KiB 的块，整份文件从不进内存。这正是 RemoteControl 的
``get_uploaded_files`` 做不到的事——它按一份答卷把全部文件 base64 塞进一个 JSON 应答。

状态码是**永久与暂时的分界**，不能含糊：

===== =========================== =========================================
状态   含义                        平台怎么办
===== =========================== =========================================
200   字节流                      落盘
404   引擎里已经没有这一份         记为缺失，不重试
413   超出约定的单份上限           记为超限，不重试
502   通道不可达 / 应答不可信       退避后重试，**绝不**记为缺失
===== =========================== =========================================

把 502 和 404 混起来，就会让一次网络抖动变成一份悄悄缺失的附件——比报错危险得多。

附件内容是个人数据：本模块的日志只记实例、sid、答卷号与**引擎生成的**存储名，
从不记内容、不记作答者起的原始文件名（它根本不传到这里）、不记密钥或含 ``sig`` 的 URL。
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from .auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, AuthError, verify
from .channel import (
    AttachmentClient,
    AttachmentStream as ChannelStream,
    AttachmentTooLarge,
    ChannelError,
    InvalidChannelRequest,
)
from .engines import EngineConfig, is_valid_instance_id
from .service import Response

log = logging.getLogger("pubgw.attachments")

ATTACHMENT_PATH = "/v1/responses/attachment"
#: 平台能要求的单份上限的硬顶。平台自己也有一个（默认 64 MiB），这里只挡住离谱的值。
MAX_ATTACHMENT_BYTES = 512 * 1024 * 1024
CONTENT_TYPE = "application/octet-stream"

_FIELDS = frozenset({
    "engineInstanceId", "surveyId", "generation", "responseId", "field", "storedName", "maxBytes",
})
_GENERATION = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\Z")
_FIELD_NAME = re.compile(r"\A[A-Za-z0-9_#]{1,64}\Z")
_STORED_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
#: 参与者令牌列不是作答列，更不可能是上传列。当普通列要就绕过了匿名判定（同 responses.py）。
_TOKEN_COLUMN = "token"

ChannelFactory = Callable[[EngineConfig], Optional[AttachmentClient]]


class InvalidAttachmentRequest(ValueError):
    """请求体不合法。消息只进服务端日志。"""


@dataclass(frozen=True)
class AttachmentRequest:
    engine_instance_id: str
    survey_id: int
    generation: str
    response_id: int
    field: str
    stored_name: str
    max_bytes: int


class AttachmentStream:
    """一次成功取件的应答：状态、内容类型、长度，以及一块一块给出的字节。

    与 ``Response``（状态＋完整字节）并列，服务器外壳按类型分别写出。
    """

    status = 200
    content_type = CONTENT_TYPE

    def __init__(self, stream: ChannelStream):
        self._stream = stream
        self.length = stream.length

    def chunks(self):
        return self._stream.chunks()

    def close(self) -> None:
        self._stream.close()


def parse_attachment_request(body: bytes) -> AttachmentRequest:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidAttachmentRequest("body is not UTF-8 JSON") from None
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise InvalidAttachmentRequest("body must be an object with exactly {}".format(sorted(_FIELDS)))
    instance = payload["engineInstanceId"]
    if not is_valid_instance_id(instance):
        raise InvalidAttachmentRequest("engineInstanceId is not a valid instance id")
    return AttachmentRequest(
        engine_instance_id=instance,
        survey_id=_positive(payload["surveyId"], "surveyId"),
        generation=_matching(payload["generation"], _GENERATION, "generation"),
        response_id=_positive(payload["responseId"], "responseId"),
        field=_column(payload["field"]),
        stored_name=_stored_name(payload["storedName"]),
        max_bytes=_bounded(payload["maxBytes"]),
    )


def _positive(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidAttachmentRequest("{} must be a positive integer".format(what))
    return value


def _bounded(value: Any) -> int:
    size = _positive(value, "maxBytes")
    if size > MAX_ATTACHMENT_BYTES:
        raise InvalidAttachmentRequest("maxBytes exceeds the gateway ceiling")
    return size


def _matching(value: Any, pattern, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise InvalidAttachmentRequest("{} is outside the grammar".format(what))
    return value


def _column(value: Any) -> str:
    name = _matching(value, _FIELD_NAME, "field")
    if name == _TOKEN_COLUMN:
        raise InvalidAttachmentRequest("the token column is not an upload column")
    return name


def _stored_name(value: Any) -> str:
    name = _matching(value, _STORED_NAME, "storedName")
    if ".." in name:
        raise InvalidAttachmentRequest("storedName is outside the grammar")
    return name


def _http_channel(config: EngineConfig) -> Optional[AttachmentClient]:
    if not config.channel_secret:
        return None
    return AttachmentClient.from_rpc_url(config.rpc_url, config.instance_id, config.channel_secret)


class AttachmentReadService:
    """HTTP 无关的取件语义：认证 → 请求体 → 实例 → 经通道取件。"""

    def __init__(
        self,
        engines: Mapping[str, EngineConfig],
        secret: bytes,
        now: Callable[[], float] = time.time,
        channel_factory: Optional[ChannelFactory] = None,
    ):
        self._engines = engines
        self._secret = secret
        self._now = now
        self._channel_factory = channel_factory or _http_channel

    def fetch(self, headers: Mapping[str, str], body: bytes):
        lowered = {str(name).lower(): value for name, value in headers.items()}
        try:
            verify(self._secret, lowered.get(TIMESTAMP_HEADER.lower()),
                   lowered.get(SIGNATURE_HEADER.lower()), body, self._now())
        except AuthError as error:
            log.warning("rejected unauthenticated attachment fetch: %s", error.reason)
            return _json(401, {"error": error.reason})
        if lowered.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return _json(400, {"error": "invalid_request"})
        try:
            request = parse_attachment_request(body)
        except InvalidAttachmentRequest as error:
            log.info("rejected attachment fetch: %s", error)
            return _json(400, {"error": "invalid_request"})
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return _json(404, {"error": "unknown_engine_instance"})
        return self._fetch(engine, request)

    def _fetch(self, engine: EngineConfig, request: AttachmentRequest):
        channel = self._channel_factory(engine)
        if channel is None:
            # 配置漏了通道密钥就回 404，等于把一次配置错误变成一份静默缺失的附件。失败关闭。
            log.error("engine %s has no plugin channel configured; cannot fetch attachments",
                      engine.instance_id)
            return _json(502, {"error": "engine_error"})
        try:
            stream = channel.open(request.survey_id, request.generation, request.response_id,
                                  request.field, request.stored_name, request.max_bytes)
        except AttachmentTooLarge:
            log.info("attachment %s of sid %s response %s exceeds the limit", request.stored_name,
                     request.survey_id, request.response_id)
            return _json(413, {"error": "too_large"})
        except (ChannelError, InvalidChannelRequest) as error:
            log.warning("attachment fetch on %s sid %s: %s", engine.instance_id, request.survey_id, error)
            return _json(502, {"error": "engine_error"})
        except Exception:  # noqa: BLE001 — 兜底：对外绝不带堆栈
            log.exception("unexpected error fetching an attachment on %s", engine.instance_id)
            return _json(500, {"error": "internal_error"})
        if stream is None:
            log.info("attachment %s of sid %s response %s is gone", request.stored_name,
                     request.survey_id, request.response_id)
            return _json(404, {"error": "not_found"})
        log.info("attachment %s of sid %s response %s: %d bytes", request.stored_name,
                 request.survey_id, request.response_id, stream.length)
        return AttachmentStream(stream)


def _json(status: int, payload: Dict[str, Any]) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
