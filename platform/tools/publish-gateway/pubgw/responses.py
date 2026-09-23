"""``POST /v1/responses/read``：按答卷号读引擎作答值（契约 platform/contracts/response-read-v1.md，ADR 0013）。

平台只分页自己的答卷投影，对当前页的答卷号来这里取作答；引擎口令留在网关。网关不做业务授权：
平台只会传它租户已发布版本里记录的 (实例, sid)。

读取用 RemoteControl ``export_responses``（JSON、题目代码表头、短答案、按答卷号区间）。JSON 的键是
题目代码，重复时引擎会改名，所以**按请求列的顺序做位置对应**：请求的列固定以 ``id`` 开头，
``Writer::write`` 按 ``selectedColumns`` 的顺序输出每条记录，列数不符即视为应答不可信。

稀疏的答卷号按间距不超过 ``MAX_ID_SPAN`` 分段读取，避免一次读取被放大成整表导出。
作答是个人数据：本模块的日志只记实例、sid、条数与失败的 RPC 方法名，从不记值或引擎错误原文。
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
from .engines import EngineConfig, is_valid_instance_id
from .ranking import (
    RankingColumns,
    RankingLayoutCache,
    UntrustedRanking,
    decompose,
    missing_main_columns,
    ranking_columns,
    requested_columns,
)
from .rpc import RemoteControlClient, RpcError, Transport
from .service import LazyLoginClient, Response, http_transport

log = logging.getLogger("pubgw.responses")

READ_PATH = "/v1/responses/read"
MAX_RESPONSE_IDS = 500
MAX_FIELDS = 5000
#: 一次 export_responses 覆盖的答卷号区间宽度上限（含两端，区间内最多这么多个号）。
MAX_ID_SPAN = 200

_FIELDS = frozenset({"engineInstanceId", "surveyId", "responseIds", "fields"})
_FIELDNAME = re.compile(r"\A[A-Za-z0-9_#]{1,64}\Z")
_ID_COLUMN = "id"
#: 表里没有任何答卷（或答卷表不存在）：不是错误，请求的答卷全都不在。
_EMPTY_ERRORS = frozenset({"ERR_NO_DATA", "ERR_NO_RESPONSE_TABLE"})

TransportFactory = Callable[[EngineConfig], Transport]


class InvalidReadRequest(ValueError):
    """请求体不合法。消息只进服务端日志。"""


class UntrustedExport(RuntimeError):
    """引擎的导出结果形状不对（不是 base64 JSON、列数不符），不能据此回答。"""


@dataclass(frozen=True)
class ReadRequest:
    engine_instance_id: str
    survey_id: int
    response_ids: Tuple[int, ...]
    fields: Tuple[str, ...]


def parse_read_request(body: bytes) -> ReadRequest:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InvalidReadRequest("body is not UTF-8 JSON") from None
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise InvalidReadRequest("body must be an object with exactly {}".format(sorted(_FIELDS)))
    instance = payload["engineInstanceId"]
    if not is_valid_instance_id(instance):
        raise InvalidReadRequest("engineInstanceId is not a valid instance id")
    survey_id = payload["surveyId"]
    if not _is_positive_int(survey_id):
        raise InvalidReadRequest("surveyId must be a positive integer")
    return ReadRequest(
        engine_instance_id=instance,
        survey_id=survey_id,
        response_ids=_response_ids(payload["responseIds"]),
        fields=_fields(payload["fields"]),
    )


def _response_ids(value: Any) -> Tuple[int, ...]:
    if not isinstance(value, list) or not 0 < len(value) <= MAX_RESPONSE_IDS:
        raise InvalidReadRequest("responseIds must hold 1..{} ids".format(MAX_RESPONSE_IDS))
    if not all(_is_positive_int(item) for item in value) or len(set(value)) != len(value):
        raise InvalidReadRequest("responseIds must be distinct positive integers")
    return tuple(sorted(value))


def _fields(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list) or not 0 < len(value) <= MAX_FIELDS:
        raise InvalidReadRequest("fields must hold 1..{} names".format(MAX_FIELDS))
    if not all(isinstance(item, str) and _FIELDNAME.match(item) for item in value):
        raise InvalidReadRequest("fields must be engine column names")
    if _ID_COLUMN in value or len(set(value)) != len(value):
        raise InvalidReadRequest("fields must be distinct and must not include id")
    return tuple(value)


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def id_ranges(ids: Sequence[int], max_span: int = MAX_ID_SPAN) -> List[Tuple[int, int]]:
    """把升序答卷号切成若干闭区间，每段 ``hi - lo < max_span``。"""
    ranges: List[Tuple[int, int]] = []
    for rid in ids:
        if ranges and rid - ranges[-1][0] < max_span:
            ranges[-1] = (ranges[-1][0], rid)
        else:
            ranges.append((rid, rid))
    return ranges


class ExportClient(LazyLoginClient):
    """只读用的 RemoteControl 会话：只加一个 export_responses 调用。"""

    def export_json(self, survey_id: int, lo: int, hi: int, columns: Sequence[str]) -> Optional[bytes]:
        """区间内的导出文档（JSON 字节）；表里没有任何答卷时返回 None。"""
        result = self.call(
            "export_responses",
            [self._session(), survey_id, "json", None, "all", "code", "short", lo, hi, list(columns)],
        )
        if isinstance(result, dict):
            if result.get("error_code") in _EMPTY_ERRORS:
                return None
            raise RpcError("export_responses", result)
        if not isinstance(result, str):
            raise UntrustedExport("export_responses result is not a base64 string")
        try:
            return base64.b64decode(result, validate=True)
        except (binascii.Error, ValueError):
            raise UntrustedExport("export_responses result is not base64") from None


def read_answers(
    client: ExportClient,
    request: ReadRequest,
    layouts: Sequence[RankingColumns] = (),
) -> Dict[int, Dict[str, Optional[str]]]:
    """按区间调用导出，按位置把每条记录还原为 列名 → 值，只保留请求的答卷号。

    ``layouts`` 是这份问卷的排序题列形状：它们的名次列没有物理列，值由主列 JSON 摊出来。
    """
    wanted = set(request.response_ids)
    ranking = requested_columns(layouts, request.fields)
    # 名次要从主列算，主列没被请求时多读一列，应答时再投影掉。
    read_fields = request.fields + missing_main_columns(ranking, request.fields)
    columns = (_ID_COLUMN,) + read_fields
    found: Dict[int, Dict[str, Optional[str]]] = {}
    for lo, hi in id_ranges(request.response_ids):
        document = client.export_json(request.survey_id, lo, hi, columns)
        if document is None:
            continue
        for record in _records(document):
            values = _row_values(record, len(columns))
            rid = _as_response_id(values[0])
            if rid not in wanted:
                continue
            row = {name: _text(value) for name, value in zip(read_fields, values[1:])}
            found[rid] = _project(row, ranking, request.fields)
    return found


def _project(
    row: Dict[str, Optional[str]],
    ranking: Sequence[RankingColumns],
    fields: Sequence[str],
) -> Dict[str, Optional[str]]:
    if not ranking:
        return row
    try:
        expanded = decompose(row, ranking)
    except UntrustedRanking as error:
        raise UntrustedExport(str(error)) from None
    return {name: expanded[name] for name in fields}


def _records(document: bytes) -> List[Any]:
    try:
        parsed = json.loads(document.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UntrustedExport("export document is not JSON") from None
    records = parsed.get("responses") if isinstance(parsed, dict) else None
    if not isinstance(records, list):
        raise UntrustedExport("export document has no responses array")
    return records


def _row_values(record: Any, expected: int) -> List[Any]:
    # PHP 对 0..n-1 连续整数键的数组输出 JSON 数组，否则输出对象；两种都按位置读。
    values = list(record.values()) if isinstance(record, dict) else record
    if not isinstance(values, list) or len(values) != expected:
        raise UntrustedExport("export record has {} columns, expected {}".format(
            len(values) if isinstance(values, list) else "no", expected))
    return values


def _as_response_id(value: Any) -> Optional[int]:
    """区间内没有答卷时引擎输出一条全空记录，它的 id 是空串：不是答卷。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return json.dumps(value)
    raise UntrustedExport("export value is not a scalar")


class ResponseReadService:
    """HTTP 无关的读端点语义：认证 → 请求体 → 实例 → 读取。"""

    def __init__(
        self,
        engines: Mapping[str, EngineConfig],
        secret: bytes,
        transport_factory: TransportFactory = http_transport,
        now: Callable[[], float] = time.time,
    ):
        self._engines = engines
        self._secret = secret
        self._transport_factory = transport_factory
        self._now = now
        self._layouts = RankingLayoutCache(now)

    def read(self, headers: Mapping[str, str], body: bytes) -> Response:
        lowered = {str(name).lower(): value for name, value in headers.items()}
        try:
            verify(self._secret, lowered.get(TIMESTAMP_HEADER.lower()),
                   lowered.get(SIGNATURE_HEADER.lower()), body, self._now())
        except AuthError as error:
            log.warning("rejected unauthenticated response read: %s", error.reason)
            return _json(401, {"error": error.reason})
        if lowered.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return _json(400, {"error": "invalid_request"})
        try:
            request = parse_read_request(body)
        except InvalidReadRequest as error:
            log.info("rejected response read: %s", error)
            return _json(400, {"error": "invalid_request"})
        engine = self._engines.get(request.engine_instance_id)
        if engine is None:
            return _json(404, {"error": "unknown_engine_instance"})
        return self._read(engine, request)

    def _read(self, engine: EngineConfig, request: ReadRequest) -> Response:
        client = ExportClient(self._transport_factory(engine), engine.user, engine.password)
        try:
            # 排序题的名次列是虚列：先问一次列结构（按问卷缓存），再按它摊平主列 JSON。
            layouts = self._layouts.get(
                (engine.instance_id, request.survey_id),
                lambda: ranking_columns(client.get_fieldmap(request.survey_id)),
            )
            found = read_answers(client, request, layouts)
        except RpcError as error:
            log.warning("response read on %s sid %s: RemoteControl %s failed",
                        engine.instance_id, request.survey_id, error.method)
            return _json(502, {"error": "engine_error"})
        except UntrustedExport as error:
            log.error("response read on %s sid %s: %s", engine.instance_id, request.survey_id, error)
            return _json(502, {"error": "engine_error"})
        except Exception:  # noqa: BLE001 — 兜底：对外绝不带堆栈
            log.exception("unexpected error reading responses on %s", engine.instance_id)
            return _json(500, {"error": "internal_error"})
        finally:
            _logout(client)
        missing = [rid for rid in request.response_ids if rid not in found]
        log.info("response read on %s sid %s: %d found, %d missing",
                 engine.instance_id, request.survey_id, len(found), len(missing))
        responses = [{"id": rid, "values": found[rid]} for rid in request.response_ids if rid in found]
        return _json(200, {"responses": responses, "missing": missing})


def _logout(client: RemoteControlClient) -> None:
    try:
        client.logout()
    except Exception:  # noqa: BLE001 — 释放会话失败不影响读取结论
        log.warning("releasing the engine session after a response read failed")


def _json(status: int, payload: Dict[str, Any]) -> Response:
    return Response(status, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
