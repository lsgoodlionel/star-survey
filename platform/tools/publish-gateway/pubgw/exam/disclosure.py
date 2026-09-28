"""发布期的「答案不下发」守门（契约 survey-exam-v1 §4）。

引擎会把 ExpressionScript 翻成页面 JS：`_ProcessRelevance()` 里那句
`GetJavaScriptEquivalentOfExpression()`（em_manager_helper.php:4351）把条件写成
`LEMrel<qid>()`，计算值题的 equation 同理。**凡是编译成表达式的东西，作答者都能读到。**

于是有两条通道会把答案键送进浏览器，而且都是作者一不留神就会踩的：

1. 计分表（WP-03.3）。`{"question": "Q1", "points": {"A2": 5}}` 展开成
   `sum(if(Q1 == "A2", 5, 0))`，写进计算值题的 equation——那就是答案键本身。
2. 逻辑条件。`Q1 == "A2"` 当条件用，等于把正确答案摆在页面 JS 里。

两条都在这里拒绝，而不是写进文档指望作者自觉：考试场景下「作者应该注意」不是控制措施。

注意本模块只管**答案键**这一件事。作者拿错误答案做分支（`Q1 == "A1"`）是正常的
问卷逻辑，不拦。
"""

from typing import Dict, Iterable, List, Optional, Tuple

from ..model import SurveyDefinition
from ..validate import ValidationIssue
from ..logic import ast
from ..logic.model import LogicModel, expressions_of, sites
from ..logic.parser import ExpressionSyntaxError
from .schema import CHOICE, SET, AnswerKeyEntry, ExamKey


def check_disclosure(definition: SurveyDefinition, key: Optional[ExamKey]) -> List[ValidationIssue]:
    """答案键有没有被写进会编译成表达式的地方。"""
    if key is None or not key.entries:
        return []
    by_code = {entry.code: entry for entry in key.entries}
    return _scoring_issues(definition, by_code) + _logic_issues(definition, by_code)


def _scoring_issues(definition: SurveyDefinition,
                    by_code: Dict[str, AnswerKeyEntry]) -> List[ValidationIssue]:
    scope = LogicModel(definition).scope
    issues: List[ValidationIssue] = []
    for score_index, score in enumerate(definition.scoring):
        for item_index, item in enumerate(score.items):
            question = scope.by_code.get(item.question) or scope.by_uuid.get(item.question)
            if question is None or question.code not in by_code:
                continue
            issues.append(ValidationIssue(
                "E_EXAM_KEY_IN_SCORING",
                "scoring[{}].items[{}].question".format(score_index, item_index),
                "题目 {} 已经有考试答案，不能再进计分表：计分表会编译成计算值题的 equation，"
                "引擎会把它翻成页面 JS，等于把答案发给作答者".format(question.code),
            ))
    return issues


def _logic_issues(definition: SurveyDefinition,
                  by_code: Dict[str, AnswerKeyEntry]) -> List[ValidationIssue]:
    scope = LogicModel(definition).scope
    issues: List[ValidationIssue] = []
    for site in sites(definition):
        try:
            trees = expressions_of(site)
        except ExpressionSyntaxError:
            continue  # 语法问题由 logic/check.py 报告，这里不重复
        for tree in trees:
            for code, answer in _disclosures(tree, scope, by_code):
                issues.append(ValidationIssue(
                    "E_EXAM_KEY_IN_LOGIC", site.path,
                    "这里拿题目 {} 的正确答案 {!r} 做条件；条件会被引擎翻成页面 JS，"
                    "等于把答案发给作答者".format(code, answer),
                ))
    return issues


def _disclosures(node, scope, by_code: Dict[str, AnswerKeyEntry]) -> Iterable[Tuple[str, str]]:
    """语法树里所有「把有答案的题与它自己的正确答案相比」的地方。"""
    if isinstance(node, ast.Binary):
        if node.op in ("==", "!="):
            hit = _compares_to_answer(node.left, node.right, scope, by_code)
            if hit is not None:
                yield hit
        yield from _disclosures(node.left, scope, by_code)
        yield from _disclosures(node.right, scope, by_code)
    elif isinstance(node, ast.Unary):
        yield from _disclosures(node.operand, scope, by_code)
    elif isinstance(node, ast.Call):
        for argument in node.args:
            yield from _disclosures(argument, scope, by_code)
    elif isinstance(node, ast.InList):
        yield from _in_list(node, scope, by_code)


def _in_list(node: ast.InList, scope, by_code) -> Iterable[Tuple[str, str]]:
    # `Q1 in ["A2", "A3"]`：只要列表里出现正确答案就是泄漏。
    entry = _entry_of(node.item, scope, by_code)
    for option in node.options:
        if entry is not None and isinstance(option, ast.String) and option.value in entry.correct:
            yield entry.code, option.value
        yield from _disclosures(option, scope, by_code)
    # `"SQ001" in QMULTI`：多选题的成员写在左边，题目在 container 里。
    container_entry = _entry_of(node.container, scope, by_code) if node.container is not None else None
    if container_entry is not None and isinstance(node.item, ast.String) \
            and node.item.value in container_entry.correct:
        yield container_entry.code, node.item.value
    if node.container is not None:
        yield from _disclosures(node.container, scope, by_code)


def _compares_to_answer(left, right, scope, by_code) -> Optional[Tuple[str, str]]:
    for ref, other in ((left, right), (right, left)):
        entry = _entry_of(ref, scope, by_code)
        if entry is None or not isinstance(other, ast.String):
            continue
        if entry.kind in (CHOICE, SET) and other.value in entry.correct:
            return entry.code, other.value
        if entry.kind not in (CHOICE, SET) and _matches_text(entry, other.value):
            return entry.code, other.value
    return None


def _matches_text(entry: AnswerKeyEntry, value: str) -> bool:
    candidate = value.strip() if entry.trim else value
    for answer in entry.correct:
        wanted = answer.strip() if entry.trim else answer
        if candidate == wanted or (entry.ignore_case and candidate.lower() == wanted.lower()):
            return True
    return False


def _entry_of(node, scope, by_code: Dict[str, AnswerKeyEntry]) -> Optional[AnswerKeyEntry]:
    if not isinstance(node, ast.Ref):
        return None
    question = scope.by_uuid.get(node.target) if node.by_uuid else scope.by_code.get(node.target)
    return None if question is None else by_code.get(question.code)
