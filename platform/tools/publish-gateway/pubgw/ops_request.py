"""契约 v1.2 两个接口的请求体解析（系统边界，与 request.py 同样严格：多一个、少一个、类型不对都是 400）。

- ``POST /v1/close``：``requestId``（UUID，只用于日志关联）、``engineInstanceId``、``surveyId``（正整数）。
- ``POST /v1/drift-check``：``engineInstanceId``、``surveyId``、``expectedFingerprint``，可选 ``binding``
  （平台存档的绑定记录原文）。带 ``binding`` 时它必须正是所指的那份：同一实例、同一 sid、同一指纹。
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .binding import BindingRecord
from .request import InvalidRequest

_UUID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_FINGERPRINT = re.compile(r"\A[a-z][a-z0-9]{0,15}:[0-9a-f]{1,64}\Z")
_MAX_INSTANCE_ID_LENGTH = 128
_CLOSE_FIELDS = frozenset({"requestId", "engineInstanceId", "surveyId"})
_DRIFT_REQUIRED = frozenset({"engineInstanceId", "surveyId", "expectedFingerprint"})
_DRIFT_OPTIONAL = frozenset({"binding"})


@dataclass(frozen=True)
class CloseRequest:
    request_id: str
    engine_instance_id: str
    survey_id: int


@dataclass(frozen=True)
class DriftCheckRequest:
    engine_instance_id: str
    survey_id: int
    expected_fingerprint: str
    binding: Optional[BindingRecord]


def parse_close_request(body: bytes) -> CloseRequest:
    payload = _object(body)
    if set(payload) != _CLOSE_FIELDS:
        raise InvalidRequest("close body must have exactly {}, got {}".format(sorted(_CLOSE_FIELDS), sorted(payload)))
    request_id = payload["requestId"]
    if not isinstance(request_id, str) or not _UUID.match(request_id):
        raise InvalidRequest("requestId must be a UUID")
    return CloseRequest(
        request_id=request_id.lower(),
        engine_instance_id=_instance(payload["engineInstanceId"]),
        survey_id=_survey_id(payload["surveyId"]),
    )


def parse_drift_request(body: bytes) -> DriftCheckRequest:
    payload = _object(body)
    keys = set(payload)
    if not _DRIFT_REQUIRED <= keys or not keys <= _DRIFT_REQUIRED | _DRIFT_OPTIONAL:
        raise InvalidRequest("drift-check body has fields {}".format(sorted(keys)))
    instance = _instance(payload["engineInstanceId"])
    survey_id = _survey_id(payload["surveyId"])
    expected = payload["expectedFingerprint"]
    if not isinstance(expected, str) or not _FINGERPRINT.match(expected):
        raise InvalidRequest("expectedFingerprint must look like fm1:<hex>")
    binding = _binding(payload.get("binding"))
    if binding is not None and (
        binding.engine_instance != instance
        or binding.survey_id != survey_id
        or binding.fingerprint != expected
    ):
        raise InvalidRequest("binding does not describe the named survey")
    return DriftCheckRequest(instance, survey_id, expected, binding)


def _object(body: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidRequest("body is not UTF-8 JSON: {}".format(error)) from None
    if not isinstance(payload, dict):
        raise InvalidRequest("body must be a JSON object")
    return payload


def _instance(value: Any) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= _MAX_INSTANCE_ID_LENGTH:
        raise InvalidRequest("engineInstanceId must be a non-empty string")
    return value


def _survey_id(value: Any) -> int:
    # bool 是 int 的子类，必须单独排除
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidRequest("surveyId must be a positive integer")
    return value


def _binding(value: Any) -> Optional[BindingRecord]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidRequest("binding must be an object")
    try:
        return BindingRecord.from_dict(value)
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidRequest("binding is malformed: {!r}".format(error)) from None
