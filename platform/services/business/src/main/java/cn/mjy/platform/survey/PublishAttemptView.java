package cn.mjy.platform.survey;

import java.util.List;
import java.util.UUID;

/**
 * 一次发布尝试的记录。outcome：in_flight / published / failed / unknown。
 * orphanEngineSid 非空表示网关回滚失败、引擎里残留了一份问卷，需要人工清理。
 */
public record PublishAttemptView(
        UUID requestId,
        String engineInstanceId,
        int draftVersion,
        String outcome,
        Integer gatewayStatus,
        String failedStage,
        List<String> failures,
        Integer orphanEngineSid,
        int tries) {

    public PublishAttemptView {
        failures = List.copyOf(failures);
    }
}
