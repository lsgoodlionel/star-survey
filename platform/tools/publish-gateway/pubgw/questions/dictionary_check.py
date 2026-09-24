"""定义里的字典快照与题目引用之间的对账（R02-03，ADR 0019）。

题目说「我用 cn-admin-divisions 的 2024.1 版，摘要是 dg1:…」，定义顶层说「这是 cn-admin-divisions
的 2024.1 版，摘要是 dg1:…，节点在这里」。两边对不上就不该发出去：

- 引用了却没随定义下发 → 引擎上没有这一版，插件判定不了路径，题目必答会把每个人都留在页面上；
- 下发的摘要与引用的不一致 → 有一边被改过，发出去等于让服务端按另一本字典判定；
- 下发了却没人用 → 白白把几十万字符搬到引擎，而且多半是引用写错了。

**摘要不在网关重算**。平台的 dg1 覆盖的是平台侧节点的规范形式；网关重算一遍等于再造一份
规范化规则，分歧会误挡正常发布（ADR 0016 决定 5 已经为此付过一次代价）。网关只做对账，
真正的核对在引擎侧：插件按 (字典, 版本, 摘要) 判断装着的那份是不是同一份。
"""

from typing import Dict, List, Tuple

from ..model import SurveyDefinition
from ..validate import ValidationIssue
from .theme_dictionary import depth_of, reference_of, snapshot_issues
from .theme_specs import THEMES
from .themes import read_theme_options

MISSING = "E_DICTIONARY_MISSING"
DIGEST = "E_DICTIONARY_DIGEST"
SHAPE = "E_DICTIONARY_SHAPE"
DEPTH = "E_DICTIONARY_DEPTH"
UNUSED = "E_DICTIONARY_UNUSED"

_CASCADING = "mjy-cascading-select"


def check_dictionaries(definition: SurveyDefinition) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    shipped, shape_issues = _shipped(definition)
    issues.extend(shape_issues)
    if shape_issues:
        # 快照自己就不成立时不再对账：后面每条都会再报一遍，作者拿到的是噪音。
        return issues
    used = set()
    resolved, unreadable = _references(definition)
    for where, values in resolved:
        code, version, digest = reference_of(values)
        snapshot = shipped.get((code, version))
        if snapshot is None:
            issues.append(ValidationIssue(
                MISSING, where + ".themeOptions.dictionary",
                "定义里没有字典 {} 的 {} 版快照".format(code, version)))
            continue
        used.add((code, version))
        if snapshot["digest"] != digest:
            issues.append(ValidationIssue(
                DIGEST, where + ".themeOptions.dictionaryDigest",
                "题目引用的摘要与随定义下发的那一份不一致"))
            continue
        levels = len(values.get("levels") or ())
        depth = depth_of(snapshot["nodes"])
        if depth < levels:
            issues.append(ValidationIssue(
                DEPTH, where + ".themeOptions.levels",
                "题目声明了 {} 级，但字典 {} 的 {} 版只有 {} 层".format(levels, code, version, depth)))
    # 有题目的 themeOptions 自己就坏了时不查「没人用」：那道题本来要用哪本字典无从得知，
    # 再报一条只是在真正的错误旁边加噪音。
    if not unreadable:
        issues.extend(
            ValidationIssue(UNUSED, "dictionaries[{}]".format(index),
                            "字典 {} 的 {} 版没有任何题目引用".format(code, version))
            for index, (code, version) in enumerate(_keys(definition))
            if (code, version) not in used
        )
    return issues


def _keys(definition: SurveyDefinition) -> List[Tuple[str, str]]:
    return [(str(one.get("code", "")), str(one.get("version", ""))) for one in definition.dictionaries]


def _shipped(definition: SurveyDefinition) -> Tuple[Dict[Tuple[str, str], dict], List[ValidationIssue]]:
    shipped: Dict[Tuple[str, str], dict] = {}
    issues: List[ValidationIssue] = []
    for index, one in enumerate(definition.dictionaries):
        where = "dictionaries[{}]".format(index)
        found = snapshot_issues(one, where)
        if found:
            issues.extend(ValidationIssue(code, path, message) for code, path, message in found)
            continue
        key = (one["code"], one["version"])
        if key in shipped:
            issues.append(ValidationIssue(SHAPE, where, "同一本字典的同一版下发了两次"))
            continue
        shipped[key] = one
    return shipped, issues


def _references(definition: SurveyDefinition):
    """每道多级下拉题的 (路径, 解析好的 themeOptions)，另外告诉调用方有没有解析不了的。

    themeOptions 自己有问题的题目跳过：那些问题由题型校验报，这里再报一遍只是噪音。
    """
    resolved = []
    unreadable = False
    for group_index, group in enumerate(definition.groups):
        for question_index, question in enumerate(group.questions):
            if question.theme != _CASCADING or _CASCADING not in THEMES:
                continue
            values, found = read_theme_options(question)
            if found:
                unreadable = True
                continue
            resolved.append(("groups[{}].questions[{}]".format(group_index, question_index), values))
    return resolved, unreadable
