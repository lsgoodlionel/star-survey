"""引擎实例配置：``engineInstanceId`` → RemoteControl 端点、账号、口令。

配置文件（路径来自 ``PUBGW_ENGINES_CONFIG``）是一个 JSON 对象::

    {
      "hd-engine-01": {
        "rpcUrl": "http://engine-01/index.php/admin/remotecontrol",
        "user": "admin",
        "passwordEnv": "PUBGW_ENGINE_HD01_PASSWORD"
      }
    }

口令**只**从 ``passwordEnv`` 指名的环境变量读取，文件里写口令直接拒绝。
这是系统边界：任何一处不合法都在启动时抛 ConfigError，绝不带病上线。
错误信息只提变量名，从不带口令本身。
"""

import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

_INSTANCE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ENV_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_FIELDS = frozenset({"rpcUrl", "user", "passwordEnv"})


class ConfigError(ValueError):
    """引擎实例配置不可用。"""


@dataclass(frozen=True)
class EngineConfig:
    instance_id: str
    rpc_url: str
    user: str
    password: str = field(repr=False)


def is_valid_instance_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_INSTANCE_ID.match(value))


def load_engines(path: str, env: Mapping[str, str]) -> Mapping[str, EngineConfig]:
    """读取并校验整份配置，返回只读映射。"""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as error:
        raise ConfigError("cannot read engines config {}: {}".format(path, error)) from None
    except ValueError as error:
        raise ConfigError("engines config {} is not valid JSON: {}".format(path, error)) from None

    if not isinstance(payload, dict) or not payload:
        raise ConfigError("engines config must be a non-empty JSON object")
    engines = {
        instance_id: _engine(instance_id, entry, env) for instance_id, entry in payload.items()
    }
    return MappingProxyType(engines)


def _engine(instance_id: str, entry: Any, env: Mapping[str, str]) -> EngineConfig:
    where = "engine instance {!r}".format(instance_id)
    if not is_valid_instance_id(instance_id):
        raise ConfigError("{}: id must match {}".format(where, _INSTANCE_ID.pattern))
    if not isinstance(entry, dict):
        raise ConfigError("{}: entry must be an object".format(where))
    unknown = sorted(set(entry) - _FIELDS)
    if unknown:
        raise ConfigError("{}: unknown keys {} (passwords go in environment variables)".format(where, unknown))

    rpc_url = _required_text(entry, "rpcUrl", where)
    _check_url(rpc_url, where)
    user = _required_text(entry, "user", where)
    password_env = _required_text(entry, "passwordEnv", where)
    if not _ENV_NAME.match(password_env):
        raise ConfigError("{}: passwordEnv is not a valid variable name".format(where))
    password = env.get(password_env, "")
    if not password:
        raise ConfigError("{}: environment variable {} is missing or empty".format(where, password_env))
    return EngineConfig(instance_id=instance_id, rpc_url=rpc_url, user=user, password=password)


def _required_text(entry: Mapping[str, Any], name: str, where: str) -> str:
    value = entry.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{}: {} must be a non-empty string".format(where, name))
    return value.strip()


def _check_url(url: str, where: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("{}: rpcUrl must be an http(s) URL".format(where))
    if parts.username is not None or parts.password is not None:
        raise ConfigError("{}: rpcUrl must not embed credentials".format(where))
