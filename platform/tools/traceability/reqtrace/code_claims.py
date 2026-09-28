"""代码里**权威**的需求归属：题型主题注册表的 ``requirement=`` 字段。

`pubgw/questions/theme_specs.py` 里每个 `ThemeSpec` 都必须声明它实现的是哪条需求
（`requirement: str` 是必填字段，不是注释）。这是全仓唯一一处结构化的需求归属，
所以拿它做反向闸门：**新加一个题型主题，就必须同时登记它的测试证据**，否则本校验红。

为什么按源码文本读而不是 import：与 `test_plugin_registry_parity.py` 同一个理由——
这个工具要能在不摆弄 `sys.path`、不依赖网关包可导入的前提下跑起来。字段形状极稳定，
而「正则不再匹配」这种退化由 `EXPECTED_AT_LEAST` 兜住，不会变成静默的假绿。
"""

import re
from pathlib import Path
from typing import Dict, List

THEME_SPECS = Path("platform/tools/publish-gateway/pubgw/questions/theme_specs.py")

#: 一个 `ThemeSpec(...)` 块里的 `name=` 与 `requirement=`。两者在源码里相邻出现。
_SPEC = re.compile(
    r'name\s*=\s*"(?P<name>[\w-]+)"\s*,\s*\n\s*label\s*=\s*"[^"]*"\s*,\s*\n\s*'
    r'requirement\s*=\s*"(?P<requirement>R\d{2}-\d{2})"',
)

#: 下限哨兵：正则哪天不再匹配（字段顺序改了、换了引号），这个数会让它红而不是静默变空。
#: 数值＝写下这条校验时注册表里的主题数，只在真的删主题时才该往下调。
EXPECTED_AT_LEAST = 15


def theme_requirements(repo_root: Path) -> Dict[str, str]:
    """主题名 → 它声明实现的需求编号。"""
    source = (repo_root / THEME_SPECS).read_text(encoding="utf-8")
    return {m.group("name"): m.group("requirement") for m in _SPEC.finditer(source)}


def missing_evidence(claims: Dict[str, str], covered: Dict[str, List]) -> List[str]:
    """声明了需求归属、却没有任何测试证据登记的主题。"""
    return [
        "题型主题 {} 声明实现 {}，但登记表里没有它的测试证据".format(name, requirement)
        for name, requirement in sorted(claims.items())
        if requirement not in covered
    ]
