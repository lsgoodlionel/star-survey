"""平台定义 → `.lss` XML。

三条原则：

1. **先校验再编译。** 编译器不接受校验不通过的定义——半个非法定义编译出来的
   LSS 一样会被引擎导入，只是代码被悄悄改掉了。
2. **所有设置写显式值。** 未指定的设置由 DEFAULT_SETTINGS 补齐，绝不留空、
   更不留继承标记（ADR 0005 决定 2）。
3. **文档里的 id 全是合成值。** 引擎导入时会重新分配 sid/gid/qid/aid
   （import_helper.php:2193-2196），这里的数字只用来在文档内部建立父子关系。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .fieldmap import definition_signature, fingerprint
from .logic.lower import lower_definition
from .model import SurveyDefinition
from .policy.compile import PLUGIN_NAME, POLICY_KEY, CompiledPolicy, compile_policy
from .validate import ValidationReport, validate_definition

COMPILER_VERSION = "pubgw-lss-1"

#: 与引擎 7.1.2 的导出保持一致（export_helper.php:1059）。
DB_VERSION = "500"

_SID = 900001
_GID_BASE = 9000
_QID_BASE = 910000
_SQID_BASE = 920000
_AID_BASE = 930000
_L10N_BASE = 940000

#: 引擎导入后可能落到问卷组默认值的设置，一律写显式值。
DEFAULT_SETTINGS = {
    "admin": "MJY Platform",
    "adminemail": "noreply@example.invalid",
    "anonymized": "N",
    "format": "G",
    "savetimings": "N",
    "datestamp": "Y",
    "usecookie": "N",
    "allowregister": "N",
    "allowsave": "Y",
    "autonumber_start": "0",
    "autoredirect": "N",
    "allowprev": "Y",
    "printanswers": "N",
    "ipaddr": "N",
    "refurl": "N",
    "publicstatistics": "N",
    "publicgraphs": "N",
    "listpublic": "N",
    "htmlemail": "Y",
    "sendconfirmation": "N",
    "tokenanswerspersistence": "N",
    "assessments": "N",
    "usecaptcha": "N",
    "usetokens": "N",
    "tokenlength": "15",
    "showxquestions": "Y",
    "showgroupinfo": "B",
    "shownoanswer": "N",
    "showqnumcode": "X",
    "bounceprocessing": "N",
    "showwelcome": "N",
    "showprogress": "Y",
    "questionindex": "0",
    "navigationdelay": "0",
    "nokeyboard": "N",
    "alloweditaftercompletion": "Y",
}

_SURVEY_FIELDS = (
    "sid", "gsid", "admin", "adminemail", "anonymized", "format", "savetimings", "template",
    "language", "additional_languages", "datestamp", "usecookie", "allowregister", "allowsave",
    "autonumber_start", "autoredirect", "allowprev", "printanswers", "ipaddr", "refurl",
    "publicstatistics", "publicgraphs", "listpublic", "htmlemail", "sendconfirmation",
    "tokenanswerspersistence", "assessments", "usecaptcha", "usetokens", "tokenlength",
    "showxquestions", "showgroupinfo", "shownoanswer", "showqnumcode", "bounceprocessing",
    "showwelcome", "showprogress", "questionindex", "navigationdelay", "nokeyboard",
    "alloweditaftercompletion",
)
_GROUP_FIELDS = ("gid", "sid", "group_order", "randomization_group", "grelevance")
_GROUP_L10N_FIELDS = ("id", "gid", "group_name", "description", "language")
_QUESTION_FIELDS = (
    "qid", "parent_qid", "sid", "gid", "type", "title", "preg", "other", "mandatory",
    "encrypted", "question_order", "scale_id", "same_default", "relevance",
    "question_theme_name", "modulename",
)
_SUBQUESTION_FIELDS = tuple(name for name in _QUESTION_FIELDS if name != "question_theme_name")
_QUESTION_L10N_FIELDS = ("id", "qid", "question", "help", "language")
_ANSWER_FIELDS = ("aid", "qid", "code", "sortorder", "assessment_value", "scale_id")
_ANSWER_L10N_FIELDS = ("id", "aid", "answer", "language")
#: 引擎导入只读 name/key/value（import_helper.php:3447），写进 lime_plugin_settings（model=Survey）。
_PLUGIN_SETTING_FIELDS = ("name", "key", "value")
#: 只有访问策略带时间窗时才出现的问卷列：没有策略的定义编译结果逐字节不变。
_POLICY_SURVEY_FIELDS = ("startdate", "expires", "access_mode")
_QUESTION_ATTRIBUTE_FIELDS = ("qid", "attribute", "value", "language")
_LANGUAGE_SETTINGS_FIELDS = (
    "surveyls_survey_id", "surveyls_language", "surveyls_title", "surveyls_description",
    "surveyls_welcometext", "surveyls_endtext", "surveyls_url", "surveyls_urldescription",
    "surveyls_dateformat", "surveyls_numberformat",
)

#: 引擎默认的题型主题名，题目没有指定主题时使用。
_DEFAULT_THEMES = {
    "L": "listradio", "!": "list_dropdown", "M": "multiplechoice",
    "P": "multiplechoice_with_comments", "F": "arrays/array", "1": "arrays/dualscale",
    "S": "shortfreetext", "T": "longfreetext", "U": "hugefreetext",
    "N": "numerical", "D": "date", "X": "boilerplate", "*": "equation",
}


class CompileError(ValueError):
    """定义没有通过校验，或者编译器无法表达它。"""

    def __init__(self, message: str, report: ValidationReport = None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class CompiledSurvey:
    """一次编译的产物：LSS 文档 ＋ 可比对的结构指纹。"""

    lss: str
    compiler_version: str
    signature: Tuple[str, ...]
    fingerprint: str
    definition_uuid: str
    #: 访问策略的编译产物（ADR 0016）；定义没有策略时为 None。
    policy: Optional[CompiledPolicy] = None


class LssCompiler:
    """把平台定义编译成引擎可导入的 `.lss`。"""

    version = COMPILER_VERSION

    def compile(self, definition: SurveyDefinition) -> CompiledSurvey:
        report = validate_definition(definition)
        if not report.is_valid:
            raise CompileError(
                "definition failed validation: "
                + ", ".join(sorted({issue.code for issue in report.issues})),
                report,
            )
        signature = definition_signature(definition)
        policy = compile_policy(definition)
        return CompiledSurvey(
            # v2 的逻辑先降到引擎层（relevance／属性／转义文本）；v1 原样通过。
            lss=self._document(lower_definition(definition), policy),
            compiler_version=self.version,
            signature=signature,
            fingerprint=fingerprint(signature),
            definition_uuid=definition.uuid,
            policy=policy,
        )

    # ------------------------------------------------------------- 文档

    def _document(self, definition: SurveyDefinition, policy: Optional[CompiledPolicy] = None) -> str:
        layout = _Layout(definition)
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<document>",
            _leaf("LimeSurveyDocType", "Survey"),
            _leaf("DBVersion", DB_VERSION),
            _languages(definition),
            _section("groups", _GROUP_FIELDS, layout.group_rows()),
            _section("group_l10ns", _GROUP_L10N_FIELDS, layout.group_l10n_rows()),
            _section("questions", _QUESTION_FIELDS, layout.question_rows()),
            _section("subquestions", _SUBQUESTION_FIELDS, layout.subquestion_rows()),
            _section("question_l10ns", _QUESTION_L10N_FIELDS, layout.question_l10n_rows()),
            _section("answers", _ANSWER_FIELDS, layout.answer_rows()),
            _section("answer_l10ns", _ANSWER_L10N_FIELDS, layout.answer_l10n_rows()),
            _section("question_attributes", _QUESTION_ATTRIBUTE_FIELDS, layout.attribute_rows()),
            _section("surveys", _survey_fields(policy), [_survey_row(definition, policy)]),
            _section("surveys_languagesettings", _LANGUAGE_SETTINGS_FIELDS, _language_rows(definition)),
            _section("plugin_settings", _PLUGIN_SETTING_FIELDS, _plugin_setting_rows(policy)),
            "</document>",
        ]
        return "\n".join(part for part in parts if part)


class _Layout:
    """给定义里的每个实体分配文档内的合成 id，并保持顺序稳定。"""

    def __init__(self, definition: SurveyDefinition):
        self._definition = definition
        self._gids: Dict[str, int] = {}
        self._qids: Dict[str, int] = {}
        for index, group in enumerate(definition.groups):
            self._gids[group.uuid] = _GID_BASE + index + 1
        for index, question in enumerate(definition.questions()):
            self._qids[question.uuid] = _QID_BASE + index + 1
        position = 0
        for question in definition.questions():
            for subquestion in question.subquestions:
                position += 1
                self._qids[subquestion.uuid] = _SQID_BASE + position

    def group_rows(self) -> List[Dict[str, str]]:
        return [
            {
                "gid": str(self._gids[group.uuid]),
                "sid": str(_SID),
                "group_order": str(index + 1),
                "randomization_group": "",
                "grelevance": group.relevance,
            }
            for index, group in enumerate(self._definition.groups)
        ]

    def group_l10n_rows(self) -> List[Dict[str, str]]:
        return [
            {
                "id": str(_L10N_BASE + index + 1),
                "gid": str(self._gids[group.uuid]),
                "group_name": group.title,
                "description": group.description,
                "language": self._definition.language,
            }
            for index, group in enumerate(self._definition.groups)
        ]

    def question_rows(self) -> List[Dict[str, str]]:
        rows = []
        for group in self._definition.groups:
            for order, question in enumerate(group.questions):
                row = self._question_row(question, self._gids[group.uuid], order + 1, parent=0)
                row["question_theme_name"] = question.theme or _DEFAULT_THEMES[question.type]
                rows.append(row)
        return rows

    def subquestion_rows(self) -> List[Dict[str, str]]:
        rows = []
        for group in self._definition.groups:
            for question in group.questions:
                for order, subquestion in enumerate(question.subquestions):
                    row = self._question_row(
                        subquestion,
                        self._gids[group.uuid],
                        order + 1,
                        parent=self._qids[question.uuid],
                        question_type=question.type,
                    )
                    rows.append(row)
        return rows

    def _question_row(self, item, gid: int, order: int, parent: int, question_type: str = "") -> Dict[str, str]:
        is_subquestion = parent != 0
        return {
            "qid": str(self._qids[item.uuid]),
            "parent_qid": str(parent),
            "sid": str(_SID),
            "gid": str(gid),
            "type": question_type if is_subquestion else item.type,
            "title": item.code,
            "preg": "",
            "other": "N" if is_subquestion else _flag(item.other),
            "mandatory": "N" if is_subquestion else _flag(item.mandatory),
            "encrypted": "N",
            "question_order": str(order),
            "scale_id": str(item.scale if is_subquestion else 0),
            "same_default": "0",
            "relevance": "1" if is_subquestion else item.relevance,
            "modulename": "",
        }

    def question_l10n_rows(self) -> List[Dict[str, str]]:
        rows = []
        identifier = _L10N_BASE
        for question in self._definition.questions():
            identifier += 1
            rows.append(self._l10n_row(identifier, question.uuid, question.text, question.help))
        for question in self._definition.questions():
            for subquestion in question.subquestions:
                identifier += 1
                rows.append(self._l10n_row(identifier, subquestion.uuid, subquestion.text, ""))
        return rows

    def _l10n_row(self, identifier: int, uuid: str, text: str, help_text: str) -> Dict[str, str]:
        return {
            "id": str(identifier),
            "qid": str(self._qids[uuid]),
            "question": text,
            "help": help_text,
            "language": self._definition.language,
        }

    def answer_rows(self) -> List[Dict[str, str]]:
        rows = []
        aid = _AID_BASE
        for question in self._definition.questions():
            for order, answer in enumerate(question.answers):
                aid += 1
                rows.append(
                    {
                        "aid": str(aid),
                        "qid": str(self._qids[question.uuid]),
                        "code": answer.code,
                        "sortorder": str(order + 1),
                        "assessment_value": str(answer.assessment_value),
                        "scale_id": str(answer.scale),
                    }
                )
        return rows

    def answer_l10n_rows(self) -> List[Dict[str, str]]:
        rows = []
        aid = _AID_BASE
        identifier = _L10N_BASE
        for question in self._definition.questions():
            for answer in question.answers:
                aid += 1
                identifier += 1
                rows.append(
                    {
                        "id": str(identifier),
                        "aid": str(aid),
                        "answer": answer.text,
                        "language": self._definition.language,
                    }
                )
        return rows

    def attribute_rows(self) -> List[Dict[str, str]]:
        rows = []
        for question in self._definition.questions():
            for name in sorted(question.attributes):
                rows.append(
                    {
                        "qid": str(self._qids[question.uuid]),
                        "attribute": name,
                        "value": question.attributes[name],
                        "language": "",
                    }
                )
        return rows


# ------------------------------------------------------------------ 问卷行


def _survey_fields(policy: Optional[CompiledPolicy]) -> Tuple[str, ...]:
    native = policy.native_settings if policy else {}
    return _SURVEY_FIELDS + tuple(name for name in _POLICY_SURVEY_FIELDS if name in native)


def _plugin_setting_rows(policy: Optional[CompiledPolicy]) -> List[Dict[str, str]]:
    if policy is None or policy.payload is None:
        return []
    return [{"name": PLUGIN_NAME, "key": POLICY_KEY, "value": policy.payload}]


def _survey_row(definition: SurveyDefinition, policy: Optional[CompiledPolicy] = None) -> Dict[str, str]:
    values = dict(DEFAULT_SETTINGS)
    values.update(definition.settings)
    values.update(policy.native_settings if policy else {})
    values.update(
        {
            "sid": str(_SID),
            "gsid": "1",
            "template": definition.theme,
            "language": definition.language,
            "additional_languages": " ".join(definition.additional_languages),
        }
    )
    return {name: values.get(name, "") for name in _survey_fields(policy)}


def _language_rows(definition: SurveyDefinition) -> List[Dict[str, str]]:
    languages = (definition.language,) + definition.additional_languages
    return [
        {
            "surveyls_survey_id": str(_SID),
            "surveyls_language": language,
            "surveyls_title": definition.title,
            "surveyls_description": definition.description,
            "surveyls_welcometext": "",
            "surveyls_endtext": "",
            "surveyls_url": "",
            "surveyls_urldescription": "",
            "surveyls_dateformat": "1",
            "surveyls_numberformat": "0",
        }
        for language in languages
    ]


# ------------------------------------------------------------------ XML


def _languages(definition: SurveyDefinition) -> str:
    languages = (definition.language,) + definition.additional_languages
    body = "\n".join("  " + _leaf("language", language) for language in languages)
    return " <languages>\n{}\n </languages>".format(body)


def _section(name: str, fields: Sequence[str], rows: Sequence[Dict[str, str]]) -> str:
    """没有行的小节整段省略：引擎的导出也是这么做的。"""
    if not rows:
        return ""
    field_lines = "\n".join("   " + _leaf("fieldname", field) for field in fields)
    row_lines = "\n".join(_row(fields, row) for row in rows)
    return (
        " <{name}>\n"
        "  <fields>\n{fields}\n  </fields>\n"
        "  <rows>\n{rows}\n  </rows>\n"
        " </{name}>"
    ).format(name=name, fields=field_lines, rows=row_lines)


def _row(fields: Sequence[str], values: Dict[str, str]) -> str:
    body = "\n".join("    " + _leaf(field, values.get(field, "")) for field in fields)
    return "   <row>\n{}\n   </row>".format(body)


def _leaf(tag: str, value: str) -> str:
    if value == "":
        return "<{}/>".format(tag)
    return "<{tag}><![CDATA[{value}]]></{tag}>".format(tag=tag, value=_cdata(value))


def _cdata(value: str) -> str:
    """CDATA 里不能出现 ']]>'，遇到就把它劈成两段。"""
    return str(value).replace("]]>", "]]]]><![CDATA[>")


def _flag(value: bool) -> str:
    return "Y" if value else "N"
