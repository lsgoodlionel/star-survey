package cn.mjy.platform.survey.gateway;

import java.util.List;

/**
 * 网关的 {@code PublishResult.to_dict()}。成功时带 binding；失败时 failedStage 与 failures 说明原因，
 * orphanSurveyId 非空表示回滚也失败了，引擎里残留一份问卷需要人工清理。
 */
public record GatewayResult(
        boolean ok,
        Integer surveyId,
        String failedStage,
        List<String> failures,
        boolean rolledBack,
        Integer orphanSurveyId,
        GatewayBinding binding) {

    public GatewayResult {
        failures = failures == null ? List.of() : List.copyOf(failures);
    }
}
