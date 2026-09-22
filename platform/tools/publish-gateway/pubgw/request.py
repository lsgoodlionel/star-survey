"""``POST /v1/publish`` 请求体的解析（系统边界）。

只认三个顶层字段：``requestId``（规范 UUID 文本）、``engineInstanceId``、``definition``。
多一个、少一个、类型不对都算 400 ``invalid_request``——契约升版之前不做宽松解析。
问卷定义本身的结构由 ``model.SurveyDefinition`` 负责，这里只确认它是个对象。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

_FIELDS = frozenset({"requestId", "engineInstanceId", "definition"})
_UUID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_MAX_INSTANCE_ID_LENGTH = 128


class InvalidRequest(ValueError):
    """请求体不合法。消息只进服务端日志，不回给调用方。"""


@dataclass(frozen=True)
class PublishRequest:
    request_id: str
    engine_instance_id: str
    definition: Mapping[str, Any]
    #: 请求内容的规范化摘要：同一 requestId 带着不同内容重放时据此识别。
    fingerprint: str


def parse_request(body: bytes) -> PublishRequest:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidRequest("body is not UTF-8 JSON: {}".format(error)) from None
    if not isinstance(payload, dict):
        raise InvalidRequest("body must be a JSON object")
    if set(payload) != _FIELDS:
        raise InvalidRequest(
            "body must have exactly the fields {}, got {}".format(sorted(_FIELDS), sorted(payload))
        )

    request_id = payload["requestId"]
    if not isinstance(request_id, str) or not _UUID.match(request_id):
        raise InvalidRequest("requestId must be a UUID")
    instance_id = payload["engineInstanceId"]
    if not isinstance(instance_id, str) or not 0 < len(instance_id) <= _MAX_INSTANCE_ID_LENGTH:
        raise InvalidRequest("engineInstanceId must be a non-empty string")
    definition = payload["definition"]
    if not isinstance(definition, dict):
        raise InvalidRequest("definition must be a JSON object")

    return PublishRequest(
        request_id=request_id.lower(),
        engine_instance_id=instance_id,
        definition=definition,
        fingerprint=_fingerprint(instance_id, definition),
    )


def _fingerprint(instance_id: str, definition: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"engineInstanceId": instance_id, "definition": definition},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
