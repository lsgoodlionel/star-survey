"""``POST /v1/responses/uploads``：把作答者上传从插件拉到平台（ADR 0019 决定 8）。

引擎的 `upload/` 卷是每租户独立的，平台够不着；引擎到平台又没有在线通道
（ADR 0008 的出网约束）。因此形状只能是**平台主动拉**，走 ADR 0018 那条已经有鉴权的
网关↔插件通道：

    平台 ──POST /v1/responses/uploads──▶ 网关 ──GET plugins/direct?function=uploadSessions──▶ 插件
                                         网关 ──GET plugins/direct?function=uploadContent ──▶ 插件

**清单只来自插件的上传会话表。** 网关自己不认识答卷字段里那张文件清单，也不该认识——
那是作答者可控的 POST 数据。本模块因此没有任何「按文件名找文件」的形状。

插件的应答是**外部数据**：形状不对就拒绝，绝不猜（与 channel.py 同一条自律）。
失败一律关闭：通道不可达、密钥没配、应答形状不对，都不能变成"这份答卷没有上传"——
那是一次静默的数据缺失，比报错危险得多。

作答者上传是个人数据：本模块的日志只记实例、sid、条数与失败类型，
**从不记文件名、不记字节、不记含 ``sig`` 的完整 URL**。
"""

import base64
import binascii
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, AuthError, verify
from .channel import (
    DIRECT_PATH,
    PLUGIN_NAME,
    ChannelError,
    InvalidChannelRequest,
    canonical_query,
    sign,
)
from .engines import EngineConfig
from .service import Response

log = logging.getLogger("pubgw.uploads")

UPLOADS_PATH = "/v1/responses/uploads"
LIST_FUNCTION = "uploadSessions"
CONTENT_FUNCTION = "uploadContent"
MAX_RESPONSE_IDS = 200
#: 单件上传的字节上限；与插件侧和平台侧的上限同量级，三处各判各的。
MAX_CONTENT_BYTES = 16777216
_TIMEOUT_SECONDS = 60
_RPC_SUFFIX = "/admin/remotecontrol"

_GENERATION = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\Z")
_UUID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_QUESTION_CODE = re.compile(r"\A[A-Za-z0-9_]{1,64}\Z")

_FIELDS = frozenset({"engineInstanceId", "surveyId", "generation"})
_OPTIONAL_FIELDS = frozenset({"responseIds", "uploadToken"})


@dataclass(frozen=True)
class UploadSession:
    """清单里的一条：**不含字节**。取字节要按 token 再来一次。"""

    upload_token: str
    question_code: str
    original_name: str
    extension: str
    size_bytes: int


@dataclass(frozen=True)
class UploadContent:
    """一件上传的字节及其在答卷里的位置。"""

    upload_token: str
    question_code: str
    response_id: int
    original_name: str
    extension: str
    content: bytes


class InvalidUploadRequest(ValueError):
    """请求体不合法。消息只进服务端日志。"""


class UploadSessionClient:
    """一个引擎实例的上传通道客户端。``fetch`` 是测试用的注入点（与 channel.py 同形）。"""

    def __init__(
        self,
        index_url: str,
        engine_instance_id: str,
        secret: str,
        fetch: Optional[Callable[[str], bytes]] = None,
        now: Optional[Callable[[], float]] = None,
    ):
        self._index_url = index_url.rstrip("/")
        self._instance_id = engine_instance_id
        self._secret = secret
        self._fetch = fetch or _urlopen
        self._now = now or time.time

    @classmethod
    def from_rpc_url(cls, rpc_url: str, engine_instance_id: str, secret: str,
                     fetch=None, now=None) -> Optional["UploadSessionClient"]:
        trimmed = rpc_url.rstrip("/")
        if not trimmed.endswith(_RPC_SUFFIX):
            return None
        return cls(trimmed[: -len(_RPC_SUFFIX)], engine_instance_id, secret, fetch=fetch, now=now)

    def list(self, survey_id: int, generation: str,
             response_ids: Sequence[int]) -> Dict[int, List[UploadSession]]:
        """一页答卷的上传清单。sid 不存在或代次不匹配时返回空字典（正常的空，不是错误）。"""
        ids = _check_response_ids(response_ids)
        params = {
            "plugin": PLUGIN_NAME,
            "function": LIST_FUNCTION,
            "sid": str(int(survey_id)),
            "generation": _check_generation(generation),
            "responseIds": ",".join(str(value) for value in ids),
            "ts": str(int(self._now())),
        }
        return _parse_manifest(self._get(params), frozenset(ids))

    def content(self, survey_id: int, generation: str, upload_token: str) -> UploadContent:
        """一件上传的字节。一次只取一件，应答体量因此有界。"""
        params = {
            "plugin": PLUGIN_NAME,
            "function": CONTENT_FUNCTION,
            "sid": str(int(survey_id)),
            "generation": _check_generation(generation),
            "uploadToken": _check_token(upload_token),
            "ts": str(int(self._now())),
        }
        return _parse_content(self._get(params), params["uploadToken"])

    def _get(self, params: Mapping[str, str]) -> bytes:
        canonical = canonical_query(params)
        signature = sign(self._secret, params["ts"], canonical)
        url = "{}{}?{}&sig={}".format(self._index_url, DIRECT_PATH, canonical, signature)
        try:
            return self._fetch(url)
        except ChannelError:
            raise
        except Exception as error:
            # 刻意不带 str(error)：它可能含完整 URL（内有 sig）。类名足够区分失败类型。
            raise ChannelError("upload channel unreachable at {} ({})".format(
                self._index_url, type(error).__name__)) from None


def _parse_manifest(body: bytes, wanted_ids: frozenset) -> Dict[int, List[UploadSession]]:
    document = _document(body)
    uploads = document.get("uploads")
    if not isinstance(uploads, dict):
        raise ChannelError("upload channel returned no uploads object")
    parsed: Dict[int, List[UploadSession]] = {}
    for raw_id, entries in uploads.items():
        response_id = _response_id(raw_id, wanted_ids)
        if not isinstance(entries, list):
            raise ChannelError("uploads for a response are not a list")
        parsed[response_id] = [_session(entry) for entry in entries]
    return parsed


def _session(raw: Any) -> UploadSession:
    if not isinstance(raw, dict):
        raise ChannelError("an upload session is not an object")
    token = raw.get("uploadToken")
    code = raw.get("questionCode")
    size = raw.get("sizeBytes")
    if not isinstance(token, str) or not _UUID.match(token):
        raise ChannelError("an upload session has no usable upload token")
    if not isinstance(code, str) or not _QUESTION_CODE.match(code):
        raise ChannelError("an upload session has no usable question code")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ChannelError("an upload session has no usable size")
    return UploadSession(token, code, _text(raw.get("originalName")), _text(raw.get("extension")), size)


def _parse_content(body: bytes, wanted_token: str) -> UploadContent:
    document = _document(body)
    token = document.get("uploadToken")
    if not isinstance(token, str) or token.lower() != wanted_token.lower():
        # 应答里出现没请求过的 token 即视为不可信（与 responses.py 同一条口径）。
        raise ChannelError("upload channel answered about a token that was not asked for")
    code = document.get("questionCode")
    response_id = document.get("responseId")
    size = document.get("sizeBytes")
    encoded = document.get("contentBase64")
    if not isinstance(code, str) or not _QUESTION_CODE.match(code):
        raise ChannelError("an upload has no usable question code")
    if not isinstance(response_id, int) or isinstance(response_id, bool) or response_id < 0:
        raise ChannelError("an upload has no usable response id")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ChannelError("an upload has no usable size")
    # 先看声明的大小再解码：不让一份撑爆的 base64 先进内存。
    if size > MAX_CONTENT_BYTES:
        raise ChannelError("an upload is larger than the {} byte limit".format(MAX_CONTENT_BYTES))
    if not isinstance(encoded, str):
        raise ChannelError("an upload carries no content")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ChannelError("an upload's content is not base64") from None
    if len(content) != size:
        # 声明与实际对不上就拒绝：截断过的字节存进平台等于一件坏资产，而没人察觉。
        raise ChannelError("an upload's declared size disagrees with its bytes")
    return UploadContent(token, code, response_id, _text(document.get("originalName")),
                         _text(document.get("extension")), content)


def _document(body: bytes) -> Mapping[str, Any]:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ChannelError("upload channel returned a body that is not JSON") from None
    if not isinstance(document, dict):
        raise ChannelError("upload channel returned a body that is not an object")
    if "error" in document:
        # 401 的体绝不能被当成「没有上传」——那会让一次密钥配错变成静默的数据缺失。
        raise ChannelError("upload channel refused the read")
    return document


def _response_id(raw: Any, wanted: frozenset) -> int:
    try:
        response_id = int(str(raw))
    except ValueError:
        raise ChannelError("uploads are keyed by something that is not a response id") from None
    if response_id not in wanted:
        raise ChannelError("uploads name a response that was not asked for")
    return response_id


def _text(raw: Any) -> str:
    return raw if isinstance(raw, str) else ""


def _check_response_ids(response_ids: Sequence[int]) -> Tuple[int, ...]:
    ids = tuple(int(value) for value in response_ids)
    if not 1 <= len(ids) <= MAX_RESPONSE_IDS:
        raise InvalidChannelRequest("response ids must be 1..{}".format(MAX_RESPONSE_IDS))
    if len(set(ids)) != len(ids):
        raise InvalidChannelRequest("response ids must be distinct")
    if any(value <= 0 for value in ids):
        raise InvalidChannelRequest("response ids must be positive")
    return tuple(sorted(ids))


def _check_generation(generation: str) -> str:
    if not _GENERATION.match(str(generation)):
        raise InvalidChannelRequest("generation is outside the grammar")
    return str(generation)


def _check_token(upload_token: str) -> str:
    if not _UUID.match(str(upload_token)):
        raise InvalidChannelRequest("upload token is outside the grammar")
    return str(upload_token)


@dataclass(frozen=True)
class UploadRequest:
    engine_instance_id: str
    survey_id: int
    generation: str
    response_ids: Tuple[int, ...]
    upload_token: str

    def wants_content(self) -> bool:
        return self.upload_token != ""


def parse_upload_request(body: bytes) -> UploadRequest:
    """严格按契约解析。封闭字段集：出现任何其他字段即拒绝。"""
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidUploadRequest("body is not JSON") from None
    if not isinstance(document, dict):
        raise InvalidUploadRequest("body is not an object")
    unknown = set(document) - _FIELDS - _OPTIONAL_FIELDS
    if unknown:
        # 「按文件名要文件」这类形状就是在这里被挡住的：契约里根本没有那个字段。
        raise InvalidUploadRequest("unknown field(s) in the request body")
    missing = _FIELDS - set(document)
    if missing:
        raise InvalidUploadRequest("missing field(s) in the request body")
    survey_id = document["surveyId"]
    if not isinstance(survey_id, int) or isinstance(survey_id, bool) or survey_id <= 0:
        raise InvalidUploadRequest("surveyId must be a positive integer")
    instance_id = document["engineInstanceId"]
    if not isinstance(instance_id, str) or not instance_id:
        raise InvalidUploadRequest("engineInstanceId must be a string")
    generation = document["generation"]
    if not isinstance(generation, str) or not _GENERATION.match(generation):
        # 代次是上传会话自然键的一段；没有它无法保证不串代次，宁可拒绝也不读。
        raise InvalidUploadRequest("generation is outside the grammar")
    token = document.get("uploadToken", "")
    ids = document.get("responseIds", [])
    if token and ids:
        raise InvalidUploadRequest("name either responseIds or uploadToken, not both")
    if not token and not ids:
        raise InvalidUploadRequest("name either responseIds or uploadToken")
    if token:
        if not isinstance(token, str) or not _UUID.match(token):
            raise InvalidUploadRequest("uploadToken is outside the grammar")
        return UploadRequest(instance_id, survey_id, generation, (), token)
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_RESPONSE_IDS:
        raise InvalidUploadRequest("responseIds must be a list of 1..{}".format(MAX_RESPONSE_IDS))
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in ids):
        raise InvalidUploadRequest("responseIds must be positive integers")
    return UploadRequest(instance_id, survey_id, generation, tuple(sorted(set(ids))), "")


def _urlopen(url: str) -> bytes:
    """生产用的取回。超时比副表作答那条长：一件上传可以到 16 MiB。

    错误里**刻意不带** ``str(error)``——它可能含完整 URL（内有 ``sig``）。类名足够区分失败类型。
    """
    from http.client import HTTPException
    from urllib.request import Request, urlopen

    try:
        with urlopen(Request(url, method="GET"), timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except (OSError, HTTPException) as error:
        raise ChannelError("upload channel transport failed ({})".format(type(error).__name__)) from None


def _http_channel(config: EngineConfig, now: Callable[[], float]) -> Optional[UploadSessionClient]:
    if not config.channel_secret:
        return None
    return UploadSessionClient.from_rpc_url(
        config.rpc_url, config.instance_id, config.channel_secret, now=now)


class UploadReadService:
    """HTTP 无关的端点语义：认证 → 请求体 → 实例 → 拉取。"""

    def __init__(
        self,
        engines: Mapping[str, EngineConfig],
        secret: bytes,
        now: Callable[[], float] = time.time,
        channel_factory: Optional[Callable[[EngineConfig], Any]] = None,
    ):
        self._engines = engines
        self._secret = secret
        self._now = now
        self._channel_factory = channel_factory or (lambda config: _http_channel(config, now))

    def read(self, headers: Mapping[str, str], body: bytes) -> Response:
        lowered = {str(name).lower(): value for name, value in headers.items()}
        try:
            verify(self._secret, lowered.get(TIMESTAMP_HEADER.lower()),
                   lowered.get(SIGNATURE_HEADER.lower()), body, self._now())
        except AuthError as error:
            log.warning("rejected unauthenticated upload read: %s", error.reason)
            return _json(401, {"error": error.reason})
        if lowered.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return _json(400, {"error": "invalid_request"})
        try:
            request = parse_upload_request(body)
        except InvalidUploadRequest as error:
            log.info("rejected upload read: %s", error)
            return _json(400, {"error": "invalid_request"})
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return _json(404, {"error": "unknown_engine_instance"})
        channel_client = self._channel_factory(engine)
        if channel_client is None:
            # 配置漏了通道密钥就安静地少返回上传，等于一次静默的数据缺失。
            log.warning("engine %s has no plugin channel configured", engine.instance_id)
            return _json(502, {"error": "engine_error"})
        try:
            return self._pull(channel_client, engine, request)
        except (ChannelError, InvalidChannelRequest) as error:
            log.warning("upload read on %s sid %s failed: %s",
                        engine.instance_id, request.survey_id, error)
            return _json(502, {"error": "engine_error"})
        except Exception:  # noqa: BLE001 — 兜底：对外绝不带堆栈
            log.exception("unexpected error reading uploads on %s", engine.instance_id)
            return _json(500, {"error": "internal_error"})

    def _pull(self, client, engine: EngineConfig, request: UploadRequest) -> Response:
        if request.wants_content():
            found = client.content(request.survey_id, request.generation, request.upload_token)
            log.info("upload content on %s sid %s: %d bytes",
                     engine.instance_id, request.survey_id, len(found.content))
            return _json(200, {
                "uploadToken": found.upload_token,
                "questionCode": found.question_code,
                "responseId": found.response_id,
                "originalName": found.original_name,
                "extension": found.extension,
                "sizeBytes": len(found.content),
                "contentBase64": base64.b64encode(found.content).decode("ascii"),
            })
        manifest = client.list(request.survey_id, request.generation, request.response_ids)
        log.info("upload manifest on %s sid %s: %d response(s) with uploads",
                 engine.instance_id, request.survey_id, len(manifest))
        return _json(200, {"uploads": {
            str(response_id): [
                {
                    "uploadToken": session.upload_token,
                    "questionCode": session.question_code,
                    "originalName": session.original_name,
                    "extension": session.extension,
                    "sizeBytes": session.size_bytes,
                }
                for session in sessions
            ]
            for response_id, sessions in manifest.items()
        }})


def _json(status: int, payload: Dict[str, Any]) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
