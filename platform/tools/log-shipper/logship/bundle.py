"""把一次故障打包成可上传的证据包。

产出 tar.gz：manifest.json（可直接在仓库里 grep）＋ logs/ 下已脱敏的日志切片。
体积受控：每个来源只保留尾部若干行（故障现场通常在末尾），整包超限则拒绝上传
而不是悄悄截断——宁可少传，也不要让开发拿到一个看不出问题的包。
"""

import hashlib
import io
import json
import re
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .redact import Redactor

# 指纹只看错误的"形状"：数字、时间戳、十六进制 id 都会变，必须先归一化。
_VOLATILE_PATTERNS = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"), "<TS>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<HEX>"),
    (re.compile(r"\b\d+\b"), "<N>"),
]


@dataclass(frozen=True)
class BundleLimits:
    max_lines_per_source: int = 2000
    max_bundle_bytes: int = 5 * 1024 * 1024


@dataclass(frozen=True)
class IncidentContext:
    app: str
    instance: str
    environment: str
    version: str
    error_summary: str
    occurred_at: str
    suppressed_count: int = 0


@dataclass(frozen=True)
class Bundle:
    path: Path
    fingerprint: str
    manifest: Dict
    size_bytes: int


class BundleBuilder:
    def __init__(self, redactor: Redactor, limits: BundleLimits):
        self._redactor = redactor
        self._limits = limits

    def build(self, context: IncidentContext, sources: Dict[str, str], target: Path) -> Bundle:
        fingerprint = self.fingerprint(context)
        redacted_sources = {}
        redaction_counts: Dict[str, int] = {}
        for name, text in sources.items():
            result = self._redactor.redact(self._tail(text))
            redacted_sources[name] = result.text
            for category, hits in result.counts.items():
                redaction_counts[category] = redaction_counts.get(category, 0) + hits

        manifest = {
            "schemaVersion": 1,
            "app": context.app,
            "instance": context.instance,
            "environment": context.environment,
            "version": context.version,
            "errorSummary": context.error_summary,
            "occurredAt": context.occurred_at,
            "packedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fingerprint": fingerprint,
            "suppressedCount": context.suppressed_count,
            "sources": sorted(redacted_sources),
            "redaction": {"counts": redaction_counts, "policy": "mandatory"},
        }

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(target, "w:gz") as archive:
            self._add(archive, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, text in redacted_sources.items():
                self._add(archive, f"logs/{name}", text)

        size = target.stat().st_size
        if size > self._limits.max_bundle_bytes:
            target.unlink(missing_ok=True)
            raise ValueError(
                f"bundle {size} bytes exceeds limit {self._limits.max_bundle_bytes}; "
                "narrow the time window or lower max_lines_per_source"
            )
        return Bundle(path=target, fingerprint=fingerprint, manifest=manifest, size_bytes=size)

    @staticmethod
    def fingerprint(context: IncidentContext) -> str:
        normalised = context.error_summary
        for pattern, placeholder in _VOLATILE_PATTERNS:
            normalised = pattern.sub(placeholder, normalised)
        material = f"{context.app}|{context.environment}|{normalised}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def _tail(self, text: str) -> str:
        lines = text.splitlines()
        if len(lines) <= self._limits.max_lines_per_source:
            return text
        dropped = len(lines) - self._limits.max_lines_per_source
        kept = lines[-self._limits.max_lines_per_source:]
        return "\n".join([f"... truncated {dropped} earlier lines ..."] + kept) + "\n"

    @staticmethod
    def _add(archive: tarfile.TarFile, name: str, text: str) -> None:
        payload = text.encode("utf-8")
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        info.mtime = int(time.time())
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(payload))
