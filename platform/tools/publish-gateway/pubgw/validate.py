"""发布前校验：把引擎不查、或查了也只会静默纠正的东西全部挡在门外。

三类问题在这里被拦住：

1. **引擎根本不查的**。RemoteControl 的 activate_survey 只跑 checkHasGroup()
   与 checkGroup()（remotecontrol_handle.php:573-575），不跑 checkQuestions()，
   所以一道没有任何选项的单选题可以被顺利激活（ADR 0005 决定 5）。
2. **继承值**。lime_surveys_groupsettings 不随 LSS 导出，值为 'I' / 'inherit' /
   '-1' 的设置会在目标实例解析成目标侧的组默认值，同一份包行为不同
   （ADR 0005 决定 2）。
3. **代码合法性**。非法或冲突的代码会被导入端自动改名（见 codes.py）。

校验一次性收集所有问题，不在第一条就返回：作者端要的是一张完整清单。
"""

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import codes as code_rules
from .model import Question, SurveyDefinition
from .qtypes import SUPPORTED_TYPES, shape_of

#: 会被平台强制写成显式值的行为关键设置。缺一项就拒绝发布，
#: 因为缺失等价于「让目标实例的问卷组替我做决定」。
REQUIRED_EXPLICIT_SETTINGS = (
    "anonymized",
    "datestamp",
    "savetimings",
    "ipaddr",
    "refurl",
    "allowsave",
    "allowprev",
    "alloweditaftercompletion",
    "format",
    "questionindex",
)

#: 各类继承标记（SurveysGroupsettings::setToInherit()）。
_INHERIT_CHAR = "I"
_INHERIT_TEXT = "inherit"
_INHERIT_INTEGER = "-1"
_INHERIT_CAPTCHA = "E"

_CHAR_SETTINGS = frozenset(
    {
        "anonymized", "savetimings", "datestamp", "usecookie", "allowregister", "allowsave",
        "autoredirect", "allowprev", "printanswers", "ipaddr", "ipanonymize", "refurl",
        "publicstatistics", "publicgraphs", "listpublic", "htmlemail", "sendconfirmation",
        "tokenanswerspersistence", "assessments", "showxquestions", "showgroupinfo",
        "shownoanswer", "preselectnoanswer", "showqnumcode", "showwelcome", "showprogress",
        "alloweditaftercompletion", "showregisterpolicy", "showtokenpolicy", "format",
    }
)
_INTEGER_SETTINGS = frozenset({"tokenlength", "questionindex", "navigationdelay"})
_TEXT_SETTINGS = frozenset(
    {"admin", "adminemail", "template", "bounce_email", "emailresponseto", "emailnotificationto"}
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: Tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def to_dict(self) -> Dict[str, object]:
        return {
            "valid": self.is_valid,
            "issues": [
                {"code": issue.code, "path": issue.path, "message": issue.message}
                for issue in self.issues
            ],
        }


def validate_definition(definition: SurveyDefinition) -> ValidationReport:
    # 逻辑检查依赖本模块的 ValidationIssue，延迟导入以免循环引用。
    from .logic.check import check_logic, check_question_shape
    from .questions.check import check_question_type

    issues: List[ValidationIssue] = []
    issues.extend(_check_settings(definition))
    issues.extend(_check_structure(definition))
    issues.extend(_check_uniqueness(definition))
    for group_index, group in enumerate(definition.groups):
        for question_index, question in enumerate(group.questions):
            where = "groups[{}].questions[{}]".format(group_index, question_index)
            issues.extend(_check_question(question, where))
            issues.extend(check_question_shape(question, where))
            issues.extend(check_question_type(question, where))
    issues.extend(check_logic(definition))
    return ValidationReport(tuple(issues))


# ------------------------------------------------------------------ 设置


def _check_settings(definition: SurveyDefinition) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if definition.is_theme_inherited:
        issues.append(
            ValidationIssue(
                "E_INHERITED_THEME",
                "theme",
                "问卷主题不能写成 inherit：主题配置随 LSS 走，但继承关系不走",
            )
        )
    for name, value in sorted(definition.settings.items()):
        if _is_inherit_marker(name, value):
            issues.append(
                ValidationIssue(
                    "E_INHERITED_SETTING",
                    "settings.{}".format(name),
                    "设置 {} 的值 {!r} 是继承标记；LSS 不携带问卷组设置，"
                    "目标实例会用自己的组默认值解析它".format(name, value),
                )
            )
    for name in REQUIRED_EXPLICIT_SETTINGS:
        if name not in definition.settings:
            issues.append(
                ValidationIssue(
                    "E_SETTING_NOT_EXPLICIT",
                    "settings.{}".format(name),
                    "行为关键设置 {} 必须由平台写显式值".format(name),
                )
            )
    return issues


def _is_inherit_marker(name: str, value: str) -> bool:
    if name == "usecaptcha":
        return value == _INHERIT_CAPTCHA
    if name in _INTEGER_SETTINGS:
        return value.strip() == _INHERIT_INTEGER
    if name in _TEXT_SETTINGS:
        return value.strip().lower() == _INHERIT_TEXT
    if name in _CHAR_SETTINGS:
        return value == _INHERIT_CHAR
    return False


# ------------------------------------------------------------------ 结构


def _check_structure(definition: SurveyDefinition) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if not definition.groups:
        issues.append(ValidationIssue("E_EMPTY_SURVEY", "groups", "问卷至少要有一个题组"))
    for index, group in enumerate(definition.groups):
        if not group.questions:
            issues.append(
                ValidationIssue(
                    "E_EMPTY_GROUP",
                    "groups[{}]".format(index),
                    "题组 {} 没有题目，引擎的一致性检查会拒绝激活".format(group.title),
                )
            )
    return issues


def _check_uniqueness(definition: SurveyDefinition) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    issues.extend(_duplicates([group.uuid for group in definition.groups], "groups[].uuid"))
    issues.extend(_duplicates([q.uuid for q in definition.questions()], "questions[].uuid"))
    issues.extend(
        _duplicates(
            [sub.uuid for q in definition.questions() for sub in q.subquestions],
            "subquestions[].uuid",
        )
    )

    seen: Dict[str, str] = {}
    for question in definition.questions():
        if question.code in seen:
            issues.append(
                ValidationIssue(
                    "E_QUESTION_CODE_DUPLICATE",
                    "questions[{}].code".format(question.uuid),
                    "题目代码 {} 与题目 {} 重复；导入端会把后一道自动改名".format(
                        question.code, seen[question.code]
                    ),
                )
            )
            continue
        seen[question.code] = question.uuid
    return issues


def _duplicates(values: Sequence[str], path: str) -> List[ValidationIssue]:
    seen = set()
    issues = []
    for value in values:
        if value in seen:
            issues.append(
                ValidationIssue("E_DUPLICATE_UUID", path, "uuid {} 重复".format(value))
            )
            continue
        seen.add(value)
    return issues


# ------------------------------------------------------------------ 题目


def _check_question(question: Question, where: str) -> List[ValidationIssue]:
    shape = shape_of(question.type)
    if shape is None:
        return [
            ValidationIssue(
                "E_UNSUPPORTED_TYPE",
                "{}.type".format(where),
                "题型 {!r} 不在原型支持的范围内（{}）".format(
                    question.type, ",".join(SUPPORTED_TYPES)
                ),
            )
        ]

    issues: List[ValidationIssue] = []
    issues.extend(_check_question_code(question, where))
    issues.extend(_check_answers(question, shape, where))
    issues.extend(_check_subquestions(question, shape, where))
    if question.other and not shape.allows_other:
        issues.append(
            ValidationIssue(
                "E_UNEXPECTED_OTHER",
                "{}.other".format(where),
                "题型 {} 不支持「其他」选项".format(question.type),
            )
        )
    return issues


def _check_question_code(question: Question, where: str) -> List[ValidationIssue]:
    problem = code_rules.check_question_code(question.code)
    if problem is None:
        return []
    mapping = {
        code_rules.TOO_LONG: (
            "E_QUESTION_CODE_TOO_LONG",
            "题目代码最长 {} 个字符".format(code_rules.QUESTION_CODE_MAX_LENGTH),
        ),
        code_rules.INVALID: (
            "E_QUESTION_CODE_INVALID",
            "题目代码必须以字母开头，且只含字母与数字",
        ),
        code_rules.RESERVED: (
            "E_QUESTION_CODE_RESERVED",
            "题目代码是 ExpressionManager 的保留字",
        ),
    }
    issue_code, message = mapping[problem]
    return [
        ValidationIssue(
            issue_code,
            "{}.code".format(where),
            "{}：{!r}；导入端会把它自动改名".format(message, question.code),
        )
    ]


def _check_answers(question: Question, shape, where: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if not shape.needs_answers:
        if question.answers:
            issues.append(
                ValidationIssue(
                    "E_UNEXPECTED_ANSWERS",
                    "{}.answers".format(where),
                    "题型 {} 不使用答案选项".format(question.type),
                )
            )
        return issues

    for scale in shape.answer_scales:
        if question.answers_on_scale(scale):
            continue
        issue_code = "E_MISSING_ANSWERS" if scale == 0 else "E_MISSING_ANSWER_SCALE"
        issues.append(
            ValidationIssue(
                issue_code,
                "{}.answers".format(where),
                "题型 {} 的尺度 {} 没有任何答案选项；引擎的 activate_survey "
                "不跑 checkQuestions()，这样的问卷会被顺利激活成一道无法作答的题".format(
                    question.type, scale
                ),
            )
        )

    seen = set()
    for index, answer in enumerate(question.answers):
        path = "{}.answers[{}].code".format(where, index)
        issues.extend(_answer_code_issue(answer.code, path))
        key = (answer.scale, answer.code)
        if key in seen:
            issues.append(
                ValidationIssue(
                    "E_ANSWER_CODE_DUPLICATE", path, "尺度 {} 内选项代码重复".format(answer.scale)
                )
            )
        seen.add(key)
    return issues


def _answer_code_issue(code: str, path: str) -> List[ValidationIssue]:
    problem = code_rules.check_answer_code(code)
    if problem is None:
        return []
    if problem == code_rules.TOO_LONG:
        return [
            ValidationIssue(
                "E_ANSWER_CODE_TOO_LONG",
                path,
                "选项代码最长 {} 个字符（lime_answers.code）".format(
                    code_rules.ANSWER_CODE_MAX_LENGTH
                ),
            )
        ]
    return [ValidationIssue("E_ANSWER_CODE_INVALID", path, "选项代码只能含字母与数字")]


def _check_subquestions(question: Question, shape, where: str) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []
    if not shape.needs_subquestions:
        if question.subquestions:
            issues.append(
                ValidationIssue(
                    "E_UNEXPECTED_SUBQUESTIONS",
                    "{}.subquestions".format(where),
                    "题型 {} 不使用子题".format(question.type),
                )
            )
        return issues

    if not question.subquestions:
        issues.append(
            ValidationIssue(
                "E_MISSING_SUBQUESTIONS",
                "{}.subquestions".format(where),
                "题型 {} 必须至少有一个子题，否则答卷表里不会有任何一列".format(question.type),
            )
        )

    seen = set()
    for index, subquestion in enumerate(question.subquestions):
        path = "{}.subquestions[{}].code".format(where, index)
        issues.extend(_subquestion_code_issue(subquestion.code, question, path))
        key = (subquestion.scale, subquestion.code.lower())
        if key in seen:
            issues.append(
                ValidationIssue(
                    "E_SUBQUESTION_CODE_DUPLICATE",
                    path,
                    "子题代码在同一尺度内必须唯一（引擎按大小写不敏感比较）",
                )
            )
        seen.add(key)
    return issues


def _subquestion_code_issue(code: str, question: Question, path: str) -> List[ValidationIssue]:
    problem = code_rules.check_subquestion_code(code, question.type, question.other)
    if problem is None:
        return []
    mapping = {
        code_rules.TOO_LONG: ("E_SUBQUESTION_CODE_TOO_LONG", "子题代码最长 20 个字符"),
        code_rules.INVALID: ("E_SUBQUESTION_CODE_INVALID", "子题代码只能含字母与数字且不能为空"),
        code_rules.RESERVED: ("E_SUBQUESTION_CODE_RESERVED", "子题代码是引擎的保留形式"),
    }
    issue_code, message = mapping[problem]
    return [ValidationIssue(issue_code, path, "{}：{!r}".format(message, code))]
