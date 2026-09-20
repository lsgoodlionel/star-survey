"""日志脱敏。

上传前一律先脱敏：日志里可能出现答卷内容、手机号、邮箱、会话 Cookie 与各类密钥。
原则是"抹掉值、保留形状"，让开发仍能看出是哪种字段出了问题。
"""

import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Tuple

# (类别, 正则, 替换) —— 正则需保留字段名，只吃掉值。
_CREDENTIAL_PATTERNS: List[Tuple[str, str, str]] = [
    ("credential", r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b\s*[=:]\s*[\"']?[^\s\"'&;]+", r"\1=[REDACTED]"),
    ("credential", r"(?i)\bAuthorization\s*:\s*\S+\s+\S+", "Authorization: [REDACTED]"),
    ("credential", r"(?i)\b(PHPSESSID|sessionid|JSESSIONID)\s*=\s*[^\s;\"']+", r"\1=[REDACTED]"),
    ("credential", r"(?i)\bCookie\s*:\s*[^\n]+", "Cookie: [REDACTED]"),
    ("credential", r"\bgh[pousr]_[A-Za-z0-9]{16,}", "[REDACTED]"),
    ("credential", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "[REDACTED_JWT]"),
]

_PERSONAL_PATTERNS: List[Tuple[str, str, str]] = [
    ("personal", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[EMAIL]"),
    ("personal", r"\b1[3-9]\d{9}\b", "[PHONE]"),
    ("personal", r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "[ID_NUMBER]"),
    ("personal", r"\b\d{16,19}\b", "[CARD_NUMBER]"),
]

_IP_PATTERN: Tuple[str, str, str] = (
    "personal",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "[IP]",
)


@dataclass(frozen=True)
class RedactionRules:
    """脱敏规则集合，不可变；用 with_* 方法派生新规则。"""

    patterns: Tuple[Tuple[str, str, str], ...]
    mask_ip: bool = False

    @classmethod
    def default(cls) -> "RedactionRules":
        return cls(patterns=tuple(_CREDENTIAL_PATTERNS + _PERSONAL_PATTERNS))

    def with_extra_patterns(self, extras: List[Tuple[str, str]]) -> "RedactionRules":
        """extras 为 (类别, 正则)，整段匹配内容会被替换为 [REDACTED]。"""
        added = tuple((category, pattern, "[REDACTED]") for category, pattern in extras)
        return replace(self, patterns=self.patterns + added)

    def with_ip_masking(self, enabled: bool) -> "RedactionRules":
        return replace(self, mask_ip=enabled)

    def effective_patterns(self) -> Tuple[Tuple[str, str, str], ...]:
        return self.patterns + ((_IP_PATTERN,) if self.mask_ip else ())


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: Dict[str, int] = field(default_factory=dict)


class Redactor:
    """按规则替换敏感内容；对已脱敏文本再跑一次结果不变（幂等）。"""

    def __init__(self, rules: RedactionRules):
        self._compiled = [
            (category, re.compile(pattern), replacement)
            for category, pattern, replacement in rules.effective_patterns()
        ]

    def redact(self, text: str) -> RedactionResult:
        counts: Dict[str, int] = {}
        for category, regex, replacement in self._compiled:
            text, hits = regex.subn(replacement, text)
            if hits:
                counts[category] = counts.get(category, 0) + hits
        return RedactionResult(text=text, counts=counts)
