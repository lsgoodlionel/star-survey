package cn.mjy.platform.survey;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

/**
 * 一次发布尝试的记录。outcome：in_flight / published / failed / unknown。
 * orphanEngineSid 非空表示网关回滚失败、引擎里残留了一份问卷，需要人工清理。
 * nextReconcileAt 是后台核对下一次可重发的时间；manualReviewAt 非空表示结果未知的重发次数已达上限，
 * 自动核对已停止，需要人工复核（见 PublishReconciler）。
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
        int tries,
        OffsetDateTime nextReconcileAt,
        OffsetDateTime manualReviewAt) {

    public PublishAttemptView {
        failures = List.copyOf(failures);
    }
}
