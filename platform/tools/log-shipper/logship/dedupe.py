"""去重与限流。

错误往往成风暴出现：一个故障可能每秒产生上百条同样的报错。若逐条上传，
仓库会被刷爆、开发也看不出重点。这里按错误指纹做冷却，并设每日上限；
被压制的次数会累计，随下一次真正上传一起报告，避免"看起来只出现过一次"。
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict

_SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class GateLimits:
    cooldown_seconds: int = 900
    daily_limit: int = 50


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str = ""
    suppressed_count: int = 0


class ShipGate:
    """基于本地状态文件的节流阀，状态损坏时选择放行而不是静默丢弃。"""

    def __init__(self, state_path: Path, limits: GateLimits, now: Callable[[], float] = time.time):
        self._state_path = Path(state_path)
        self._limits = limits
        self._now = now

    def evaluate(self, fingerprint: str) -> GateDecision:
        state = self._load()
        entry = state["fingerprints"].get(fingerprint, {})
        suppressed = int(entry.get("suppressed", 0))
        last_shipped = float(entry.get("lastShipped", 0.0))
        now = self._now()

        if now - last_shipped < self._limits.cooldown_seconds:
            entry["suppressed"] = suppressed + 1
            state["fingerprints"][fingerprint] = entry
            self._save(state)
            return GateDecision(allowed=False, reason="cooldown", suppressed_count=suppressed + 1)

        if self._shipped_today(state, now) >= self._limits.daily_limit:
            return GateDecision(allowed=False, reason="daily_limit", suppressed_count=suppressed)

        return GateDecision(allowed=True, suppressed_count=suppressed)

    def record(self, fingerprint: str) -> None:
        """记录一次真实上传：重置该指纹的压制计数，并计入当日配额。"""
        state = self._load()
        now = self._now()
        state["fingerprints"][fingerprint] = {"lastShipped": now, "suppressed": 0}
        shipments = [moment for moment in state["shipments"] if now - moment < _SECONDS_PER_DAY]
        shipments.append(now)
        state["shipments"] = shipments
        self._save(state)

    def _shipped_today(self, state: Dict, now: float) -> int:
        return len([moment for moment in state["shipments"] if now - moment < _SECONDS_PER_DAY])

    def _load(self) -> Dict:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("fingerprints", {})
        state.setdefault("shipments", [])
        return state

    def _save(self, state: Dict) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self._state_path)
