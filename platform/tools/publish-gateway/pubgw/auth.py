"""平台 → 网关请求的 HMAC 认证（契约 publish-gateway-v1「部署与信任边界」）。

签名 = ``hex(HMAC-SHA256(secret, "<timestamp>.<原始请求体>"))``，
时间戳是 Unix 秒，偏差超过 ±300 秒即拒绝。任何一项不通过都抛 AuthError，
``reason`` 直接作为 401 响应里的 ``error`` 字段，不带任何密钥相关的信息。
"""

import hashlib
import hmac
import re
from typing import Optional

TIMESTAMP_HEADER = "X-Pubgw-Timestamp"
SIGNATURE_HEADER = "X-Pubgw-Signature"
MAX_SKEW_SECONDS = 300
MIN_SECRET_BYTES = 32

_TIMESTAMP = re.compile(r"\A[0-9]{1,12}\Z")
_SIGNATURE = re.compile(r"\A[0-9a-fA-F]{64}\Z")


class AuthError(Exception):
    """认证失败。reason 是给调用方看的稳定错误码。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def check_secret(value: str) -> bytes:
    """把环境变量里的共享密钥转成字节；不足 32 字节直接拒绝（错误信息不含密钥本身）。"""
    secret = (value or "").encode("utf-8")
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError(
            "shared secret must be at least {} bytes, got {}".format(MIN_SECRET_BYTES, len(secret))
        )
    return secret


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify(
    secret: bytes,
    timestamp: Optional[str],
    signature: Optional[str],
    body: bytes,
    now: float,
) -> None:
    """校验通过则什么都不返回；否则抛 AuthError。"""
    if not timestamp:
        raise AuthError("missing_timestamp")
    if not signature:
        raise AuthError("missing_signature")
    if not _TIMESTAMP.match(timestamp):
        raise AuthError("invalid_timestamp")
    if abs(int(timestamp) - now) > MAX_SKEW_SECONDS:
        raise AuthError("stale_timestamp")
    if not _SIGNATURE.match(signature):
        raise AuthError("bad_signature")
    expected = sign(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature.lower()):
        raise AuthError("bad_signature")
