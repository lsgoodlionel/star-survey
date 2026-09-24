package cn.mjy.platform.survey.gateway;

import java.util.List;

/**
 * 网关的 {@code PublishResult.to_dict()}。成功时带 binding；失败时 failedStage 与 failures 说明原因，
 * orphanSurveyId 非空表示回滚也失败了，引擎里残留一份问卷需要人工清理。
 *
 * <p>policyDigest 是网关回读核对过的访问策略摘要（ADR 0016，契约 survey-access-policy-v1 §5），
 * 只有带插件策略的发布才有；平台自己重算一遍再比对（{@code AccessPolicyDigest}）。
 * 老网关不认识 policy 块，回执里也就没有这个键——正是要挡住的情形。
 *
 * <p>invitations 是定义带 {@code participants} 时回读到的邀请码（契约 publish-gateway-v1 v1.2）；
 * 不带参与者的发布里是空表。
 */
public record GatewayResult(
        boolean ok,
        Integer surveyId,
        String failedStage,
        List<String> failures,
        boolean rolledBack,
        Integer orphanSurveyId,
        GatewayBinding binding,
        String policyDigest,
        List<GatewayInvitation> invitations) {

    public GatewayResult {
        failures = failures == null ? List.of() : List.copyOf(failures);
        invitations = invitations == null ? List.of() : List.copyOf(invitations);
    }

    /** 没有邀请码的结局（失败、拒收，或不需要邀请码的发布）。 */
    public GatewayResult(boolean ok, Integer surveyId, String failedStage, List<String> failures,
            boolean rolledBack, Integer orphanSurveyId, GatewayBinding binding, String policyDigest) {
        this(ok, surveyId, failedStage, failures, rolledBack, orphanSurveyId, binding, policyDigest, List.of());
    }
}
