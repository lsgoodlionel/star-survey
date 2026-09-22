"""访问密码哈希的格式：``pbkdf2-sha256$<迭代次数>$<salt base64>$<hash base64>``。

平台保存草稿时把明文换成这个格式（ADR 0016 决定 5），网关只检查格式与强度，
插件用 ``hash_pbkdf2`` ＋ ``hash_equals`` 校验。三边都只用标准库。
"""

import base64
import binascii
import hashlib
import os
from typing import Optional

SCHEME = "pbkdf2-sha256"
MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 10_000_000
DEFAULT_ITERATIONS = 600_000
MIN_SALT_BYTES = 8
HASH_BYTES = 32


def check_password_hash(value: str) -> Optional[str]:
    """合法返回 None，否则返回一句说明（不含哈希本身）。"""
    parts = value.split("$")
    if len(parts) != 4 or parts[0] != SCHEME:
        return "格式必须是 {}$<迭代次数>$<salt>$<hash>".format(SCHEME)
    iterations, salt, digest = parts[1], _b64(parts[2]), _b64(parts[3])
    if not iterations.isdigit() or not MIN_ITERATIONS <= int(iterations) <= MAX_ITERATIONS:
        return "迭代次数必须在 {} 到 {} 之间".format(MIN_ITERATIONS, MAX_ITERATIONS)
    if salt is None or len(salt) < MIN_SALT_BYTES:
        return "salt 必须是至少 {} 字节的 base64".format(MIN_SALT_BYTES)
    if digest is None or len(digest) != HASH_BYTES:
        return "hash 必须是 {} 字节的 base64".format(HASH_BYTES)
    return None


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS, salt: Optional[bytes] = None) -> str:
    """生成哈希（工具与端到端测试用；生产由平台生成）。"""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, HASH_BYTES)
    return "{}${}${}${}".format(
        SCHEME, iterations, base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")
    )


def _b64(text: str) -> Optional[bytes]:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None
