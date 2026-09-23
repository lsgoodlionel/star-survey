"""发布编排：七个阶段，任何一个失败都回滚到「什么都没发生」。

    validate → compile → import → apply → activate → verify → bind

回滚只有一个动作：``delete_survey``。引擎的一致性检查在建表之前就返回，
所以失败时不存在「半张答卷表」；``delete_survey`` 会连答卷表、参与者表、
结构行与权限行一起清掉（ADR 0005 决定 6）。

**回滚的适用范围**：``delete_survey`` 会连答卷一起删，所以它只对「从未接收过
答卷的新发布」安全。已上线问卷的结构升级不能走这条路，见 ADR 0009。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .binding import BindingRecord
from .compiler import CompiledSurvey, CompileError, LssCompiler
from .fieldmap import FINGERPRINT_VERSION, parse_fieldmap
from .invitations import InvitationError, collect
from .model import SurveyDefinition
from .policy.probe import PolicyProbe, enforcement_failures
from .rpc import RemoteControlClient, RpcError
from .validate import validate_definition
from .verify import VerificationReport, verify_publication

STAGES = ("validate", "compile", "import", "apply", "activate", "verify", "bind")

#: 继承标记：回读时出现任何一个都说明这条设置最终由目标实例的问卷组决定。
_INHERIT_MARKERS = frozenset({"I", "inherit", "-1"})

#: 回读设置时忽略的键：它们不是平台下发的值。
_SETTINGS_NOT_COMPARED = frozenset({"sid", "gsid", "active", "owner_id", "language"})


@dataclass(frozen=True)
class StageStep:
    stage: str
    ok: bool
    detail: str = ""


@dataclass
class PublishResult:
    ok: bool = False
    survey_id: Optional[int] = None
    failed_stage: Optional[str] = None
    failures: List[str] = field(default_factory=list)
    steps: List[StageStep] = field(default_factory=list)
    binding: Optional[BindingRecord] = None
    verification: Optional[VerificationReport] = None
    rolled_back: bool = False
    orphan_survey_id: Optional[int] = None
    #: 插件回读核对过的访问策略摘要（ADR 0016）；没有插件策略时为 None。
    policy_digest: Optional[str] = None
    #: 引擎生成的邀请码，按定义顺序（ADR 0016）；定义没有参与者时为 None。
    invitations: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "ok": self.ok,
            "surveyId": self.survey_id,
            "failedStage": self.failed_stage,
            "failures": list(self.failures),
            "rolledBack": self.rolled_back,
            "orphanSurveyId": self.orphan_survey_id,
            "steps": [
                {"stage": step.stage, "ok": step.ok, "detail": step.detail} for step in self.steps
            ],
            "binding": self.binding.to_dict() if self.binding else None,
            "verification": self.verification.to_dict() if self.verification else None,
        }
        if self.policy_digest is not None:
            # 只有带插件策略的发布才多这一个键：没有策略的应答与契约 v1 逐字节一致。
            payload["policyDigest"] = self.policy_digest
        if self.invitations is not None:
            # 同理：没有参与者的发布不多这个键。
            payload["invitations"] = [dict(item) for item in self.invitations]
        return payload


class Publisher:
    """把一份定义发布到一个引擎实例上，失败即回滚。"""

    def __init__(
        self,
        client: RemoteControlClient,
        engine_instance: str = "",
        compiler: Optional[LssCompiler] = None,
        clock: Callable[[], str] = None,
        policy_probe: Optional[PolicyProbe] = None,
    ):
        self._client = client
        self._engine_instance = engine_instance
        self._compiler = compiler or LssCompiler()
        self._clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._policy_probe = policy_probe

    def publish(self, definition: SurveyDefinition) -> PublishResult:
        result = PublishResult()
        compiled = self._stage_validate_and_compile(definition, result)
        if compiled is None:
            return result

        try:
            survey_id = self._client.import_survey(compiled.lss, definition.title)
        except RpcError as error:
            return self._fail(result, "import", [str(error)])
        result.survey_id = survey_id
        result.steps.append(StageStep("import", True, "sid={}".format(survey_id)))

        verification = self._stage_apply(definition, compiled, result)
        if verification is None:
            return result

        if not self._stage_activate(definition, result):
            return result

        verification = self._stage_verify(definition, compiled, result)
        if verification is None:
            return result

        result.verification = verification
        result.binding = BindingRecord(
            engine_instance=self._engine_instance,
            survey_id=survey_id,
            definition_uuid=definition.uuid,
            compiler_version=compiled.compiler_version,
            fingerprint_version=FINGERPRINT_VERSION,
            fingerprint=verification.fingerprint,
            language=definition.language,
            published_at=self._clock(),
            questions=verification.bindings,
        )
        result.steps.append(StageStep("bind", True, verification.fingerprint))
        result.ok = True
        return result

    # ------------------------------------------------------------ 阶段

    def _stage_validate_and_compile(
        self, definition: SurveyDefinition, result: PublishResult
    ) -> Optional[CompiledSurvey]:
        report = validate_definition(definition)
        if not report.is_valid:
            self._fail(
                result,
                "validate",
                ["{} {}: {}".format(i.code, i.path, i.message) for i in report.issues],
            )
            return None
        result.steps.append(StageStep("validate", True))

        try:
            compiled = self._compiler.compile(definition)
        except CompileError as error:
            self._fail(result, "compile", [str(error)])
            return None
        result.steps.append(StageStep("compile", True, compiled.fingerprint))
        return compiled

    def _stage_apply(
        self, definition: SurveyDefinition, compiled: CompiledSurvey, result: PublishResult
    ) -> Optional[VerificationReport]:
        """补发 LSS 带不动的东西，并在建答卷表之前先把结构核对一遍。"""
        try:
            failures = self._reconcile_settings(definition, result.survey_id, compiled)
            failures.extend(self._check_question_themes(definition, result.survey_id))
            failures.extend(self._check_policy(compiled, result))
            verification = self._read_and_verify(definition, compiled, result.survey_id)
        except RpcError as error:
            self._fail_and_roll_back(result, "apply", [str(error)])
            return None

        result.verification = verification
        failures.extend(_verification_failures(verification))
        if failures:
            self._fail_and_roll_back(result, "apply", failures)
            return None
        result.steps.append(StageStep("apply", True, "激活前结构核对通过"))
        return verification

    def _stage_activate(self, definition: SurveyDefinition, result: PublishResult) -> bool:
        try:
            self._client.activate_survey(result.survey_id)
            if definition.participants:
                self._client.activate_tokens(result.survey_id)
                rows = self._client.add_participants(result.survey_id, definition.participants)
                result.invitations = collect(rows, definition.participant_refs)
        except RpcError as error:
            self._fail_and_roll_back(result, "activate", [str(error)])
            return False
        except InvitationError as error:
            # 平台拿不到邀请码却以为发布成功，比发布失败更糟：路由会登记，
            # 邀请会发出去，而没有一个能打开问卷。
            result.invitations = None
            self._fail_and_roll_back(result, "activate", [str(error)])
            return False
        result.steps.append(StageStep("activate", True))
        return True

    def _stage_verify(
        self, definition: SurveyDefinition, compiled: CompiledSurvey, result: PublishResult
    ) -> Optional[VerificationReport]:
        try:
            verification = self._read_and_verify(definition, compiled, result.survey_id)
        except RpcError as error:
            self._fail_and_roll_back(result, "verify", [str(error)])
            return None
        result.verification = verification
        failures = _verification_failures(verification)
        if failures:
            self._fail_and_roll_back(result, "verify", failures)
            return None
        result.steps.append(StageStep("verify", True, verification.fingerprint))
        return verification

    # ------------------------------------------------------- 阶段的零件

    def _read_and_verify(
        self, definition: SurveyDefinition, compiled: CompiledSurvey, survey_id: int
    ) -> VerificationReport:
        rows = parse_fieldmap(self._client.get_fieldmap(survey_id))
        return verify_publication(definition, compiled, rows)

    def _reconcile_settings(
        self, definition: SurveyDefinition, survey_id: int, compiled: Optional[CompiledSurvey] = None
    ) -> List[str]:
        """回读问卷设置：出现继承标记直接失败，只是取值不同则重新下发一次。"""
        wanted = dict(definition.settings)
        wanted["template"] = definition.theme
        if compiled is not None and compiled.policy is not None:
            wanted.update(compiled.policy.native_settings)
        actual = self._client.get_survey_properties(survey_id)

        failures = []
        for name, value in sorted(wanted.items()):
            if name in _SETTINGS_NOT_COMPARED:
                continue
            current = actual.get(name)
            if isinstance(current, str) and current.strip() in _INHERIT_MARKERS:
                failures.append(
                    "E_SETTING_INHERITED_AFTER_IMPORT {}: 引擎回读到继承标记 {!r}".format(
                        name, current
                    )
                )
        if failures:
            return failures

        drifted = {
            name: value
            for name, value in wanted.items()
            if name not in _SETTINGS_NOT_COMPARED and str(actual.get(name, "")) != str(value)
        }
        if not drifted:
            return []

        self._client.set_survey_properties(survey_id, drifted)
        recheck = self._client.get_survey_properties(survey_id)
        return [
            "E_SETTING_NOT_APPLIED {}: 期望 {!r}，引擎仍然是 {!r}".format(
                name, value, recheck.get(name)
            )
            for name, value in sorted(drifted.items())
            if str(recheck.get(name, "")) != str(value)
        ]

    def _check_policy(self, compiled: CompiledSurvey, result: PublishResult) -> List[str]:
        """插件必须回读到同一份策略，否则策略会静默失效（ADR 0016 决定 4）。"""
        if compiled.policy is None or compiled.policy.digest is None:
            return []
        if self._policy_probe is None:
            return ["E_POLICY_UNVERIFIED: 没有配置插件回读通道，带访问策略的问卷不能发布"]
        failures = enforcement_failures(
            self._policy_probe(result.survey_id), result.survey_id, compiled.policy.digest
        )
        if not failures:
            result.policy_digest = compiled.policy.digest
        return failures

    def _check_question_themes(self, definition: SurveyDefinition, survey_id: int) -> List[str]:
        """题型主题不随 LSS 走，缺失时引擎静默降级（ADR 0006 决定 6）。"""
        rows = {
            str(row.get("title")): str(row.get("question_theme_name") or "")
            for row in self._client.list_questions(survey_id)
            if int(row.get("parent_qid") or 0) == 0
        }
        failures = []
        for question in definition.questions():
            if not question.theme:
                continue
            actual = rows.get(question.code)
            if actual is None:
                continue  # 代码对不上由结构校验去报，别在这里重复报一遍
            if actual != question.theme:
                failures.append(
                    "E_THEME_CHANGED {}: 期望题型主题 {!r}，引擎里是 {!r}".format(
                        question.code, question.theme, actual
                    )
                )
        return failures

    # ------------------------------------------------------------ 回滚

    def _fail(self, result: PublishResult, stage: str, failures: Sequence[str]) -> PublishResult:
        result.ok = False
        result.failed_stage = stage
        result.failures.extend(failures)
        result.steps.append(StageStep(stage, False, "; ".join(failures)[:500]))
        return result

    def _fail_and_roll_back(
        self, result: PublishResult, stage: str, failures: Sequence[str]
    ) -> PublishResult:
        self._fail(result, stage, failures)
        if result.survey_id is None:
            return result
        try:
            self._client.delete_survey(result.survey_id)
        except RpcError as error:
            result.orphan_survey_id = result.survey_id
            result.failures.append(
                "ROLLBACK FAILED sid={}: {}；引擎里留下了一个孤儿问卷，必须人工清理".format(
                    result.survey_id, error
                )
            )
            return result
        result.rolled_back = True
        result.steps.append(StageStep("rollback", True, "deleted sid={}".format(result.survey_id)))
        return result


def _verification_failures(verification: VerificationReport) -> List[str]:
    return ["{} {}".format(issue.code, issue.detail) for issue in verification.issues]
