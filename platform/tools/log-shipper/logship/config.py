"""配置加载与校验。

宁可在启动（或 doctor 体检）时报错，也不要等真出故障、最需要证据的那一刻
才发现配置写错。令牌只认环境变量名，配置文件里写了令牌也不会被使用。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from .bundle import BundleLimits
from .dedupe import GateLimits
from .redact import RedactionRules
from .sources import SourceSpec

_REQUIRED_FIELDS = ("app", "instance", "environment", "version", "repository", "tokenEnv", "stateDir")


class ConfigError(ValueError):
    """配置缺项或取值非法。"""


@dataclass(frozen=True)
class ShipperConfig:
    app: str
    instance: str
    environment: str
    version: str
    repository: str
    token_env: str
    state_dir: Path
    spool_dir: Path
    sources: List[SourceSpec]
    branch: str = "main"
    gate_limits: GateLimits = field(default_factory=GateLimits)
    bundle_limits: BundleLimits = field(default_factory=BundleLimits)
    redaction_rules: RedactionRules = field(default_factory=RedactionRules.default)
    client_certificate: str = ""

    @classmethod
    def load(cls, path: Path) -> "ShipperConfig":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigError(f"cannot read config {path}: {error}") from None
        except ValueError as error:
            raise ConfigError(f"config {path} is not valid JSON: {error}") from None
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Dict) -> "ShipperConfig":
        missing = [name for name in _REQUIRED_FIELDS if not payload.get(name)]
        if missing:
            raise ConfigError("missing required config fields: " + ", ".join(missing))

        sources = [cls._source(entry) for entry in payload.get("sources") or []]
        if not sources:
            raise ConfigError("config needs at least one entry in 'sources'")

        limits = payload.get("limits") or {}
        redaction = payload.get("redaction") or {}
        state_dir = Path(payload["stateDir"])
        return cls(
            app=payload["app"],
            instance=payload["instance"],
            environment=payload["environment"],
            version=payload["version"],
            repository=payload["repository"],
            token_env=payload["tokenEnv"],
            state_dir=state_dir,
            spool_dir=Path(payload.get("spoolDir") or state_dir / "spool"),
            sources=sources,
            branch=payload.get("branch") or "main",
            gate_limits=GateLimits(
                cooldown_seconds=int(limits.get("cooldownSeconds", 900)),
                daily_limit=int(limits.get("dailyLimit", 50)),
            ),
            bundle_limits=BundleLimits(
                max_lines_per_source=int(limits.get("maxLinesPerSource", 2000)),
                max_bundle_bytes=int(limits.get("maxBundleBytes", 5 * 1024 * 1024)),
            ),
            redaction_rules=cls._redaction(redaction),
            client_certificate=payload.get("clientCertificate") or "",
        )

    @staticmethod
    def _source(entry: Dict) -> SourceSpec:
        for key in ("name", "kind", "location"):
            if not entry.get(key):
                raise ConfigError(f"source entry misses '{key}': {entry}")
        return SourceSpec(
            name=entry["name"],
            kind=entry["kind"],
            location=entry["location"],
            lines=int(entry.get("lines", 500)),
        )

    @staticmethod
    def _redaction(payload: Dict) -> RedactionRules:
        rules = RedactionRules.default()
        extras: List[Tuple[str, str]] = [
            (str(item[0]), str(item[1])) for item in payload.get("extraPatterns") or []
        ]
        if extras:
            rules = rules.with_extra_patterns(extras)
        return rules.with_ip_masking(bool(payload.get("maskIp", False)))
