"""``POST /v1/publish`` 请求体的解析（系统边界）。

只认三个顶层字段：``requestId``（规范 UUID 文本）、``engineInstanceId``、``definition``。
多一个、少一个、类型不对都算 400 ``invalid_request``——契约升版之前不做宽松解析。
问卷定义本身的结构由 ``model.SurveyDefinition`` 负责，这里只确认它是个对象。
"""

import hashlib
import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

_FIELDS = frozenset({"requestId", "engineInstanceId", "definition", "brandAssets"})
_UUID = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
_MAX_INSTANCE_ID_LENGTH = 128
_MAX_BRAND_ASSET_BYTES = 5 * 1024 * 1024
_BRAND_ASSET_FIELDS = frozenset({"slot", "fileName", "contentType", "sizeBytes", "sha256", "contentBase64"})
_BRAND_FILES = {
    ("logoAsset", "image/png"): "mjy-brand-logo.png",
    ("logoAsset", "image/jpeg"): "mjy-brand-logo.jpg",
    ("logoAsset", "image/gif"): "mjy-brand-logo.gif",
    ("logoAsset", "image/webp"): "mjy-brand-logo.webp",
    ("faviconAsset", "image/png"): "mjy-brand-favicon.png",
    ("faviconAsset", "image/jpeg"): "mjy-brand-favicon.jpg",
    ("faviconAsset", "image/gif"): "mjy-brand-favicon.gif",
    ("faviconAsset", "image/webp"): "mjy-brand-favicon.webp",
    ("coverAsset", "image/png"): "mjy-brand-cover.png",
    ("coverAsset", "image/jpeg"): "mjy-brand-cover.jpg",
    ("coverAsset", "image/gif"): "mjy-brand-cover.gif",
    ("coverAsset", "image/webp"): "mjy-brand-cover.webp",
    ("backgroundAsset", "image/png"): "mjy-brand-background.png",
    ("backgroundAsset", "image/jpeg"): "mjy-brand-background.jpg",
    ("backgroundAsset", "image/gif"): "mjy-brand-background.gif",
    ("backgroundAsset", "image/webp"): "mjy-brand-background.webp",
}


class InvalidRequest(ValueError):
    """请求体不合法。消息只进服务端日志，不回给调用方。"""


@dataclass(frozen=True)
class PublishRequest:
    request_id: str
    engine_instance_id: str
    definition: Mapping[str, Any]
    brand_assets: tuple[Mapping[str, Any], ...]
    brand_assets_present: bool
    #: 请求内容的规范化摘要：同一 requestId 带着不同内容重放时据此识别。
    fingerprint: str


def parse_request(body: bytes) -> PublishRequest:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidRequest("body is not UTF-8 JSON: {}".format(error)) from None
    if not isinstance(payload, dict):
        raise InvalidRequest("body must be a JSON object")
    if set(payload) not in (_FIELDS, _FIELDS - {"brandAssets"}):
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

    brand_assets_present = "brandAssets" in payload
    brand_assets = payload.get("brandAssets", [])
    if not isinstance(brand_assets, list) or len(brand_assets) > 4:
        raise InvalidRequest("brandAssets must be a list of at most four items")
    brand_assets = tuple(_brand_asset(item) for item in brand_assets)
    return PublishRequest(
        request_id=request_id.lower(),
        engine_instance_id=instance_id,
        definition=definition,
        brand_assets=tuple(brand_assets),
        brand_assets_present=brand_assets_present,
        fingerprint=_fingerprint(instance_id, definition, brand_assets, brand_assets_present),
    )


def _fingerprint(instance_id: str, definition: Mapping[str, Any], brand_assets: Any = (),
                 brand_assets_present: bool = False) -> str:
    payload = {"engineInstanceId": instance_id, "definition": definition}
    if brand_assets_present:
        payload["brandAssets"] = brand_assets
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _brand_asset(item: Any) -> Mapping[str, Any]:
    """Validate bytes before engine work.  This is deliberately closed-world."""
    if not isinstance(item, dict) or set(item) != _BRAND_ASSET_FIELDS:
        raise InvalidRequest("brandAssets item has unexpected fields")
    slot, filename, content_type = item["slot"], item["fileName"], item["contentType"]
    size, digest, payload = item["sizeBytes"], item["sha256"], item["contentBase64"]
    expected = _BRAND_FILES.get((slot, content_type))
    if expected is None or filename != expected:
        raise InvalidRequest("brandAssets item has unsupported slot or file")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= _MAX_BRAND_ASSET_BYTES:
        raise InvalidRequest("brandAssets item size is outside bounds")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InvalidRequest("brandAssets item digest is invalid")
    if not isinstance(payload, str):
        raise InvalidRequest("brandAssets item payload is invalid")
    try:
        content = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        raise InvalidRequest("brandAssets item payload is not base64") from None
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise InvalidRequest("brandAssets item digest mismatch")
    return item
