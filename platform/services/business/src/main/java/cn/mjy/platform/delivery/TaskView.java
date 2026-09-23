package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryTaskRepository.TaskRow;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * 任务进度的对外视图。不含任何收件人地址。
 *
 * @param billedUnits 已确认计费的条数（按回执累计）；重复回执不会让它变大
 */
public record TaskView(
        UUID id,
        UUID surveyId,
        String channel,
        String kind,
        String status,
        UUID sourceTaskId,
        int recipientCount,
        int sentCount,
        int failedCount,
        int skippedCount,
        int billedUnits,
        int attempts,
        String lastError,
        OffsetDateTime createdAt,
        OffsetDateTime finishedAt) {

    static TaskView of(TaskRow row) {
        return new TaskView(row.id(), row.surveyId(), row.channel().code(), row.kind().code(),
                row.status().code(), row.sourceTaskId(), row.queuedCount(), row.sentCount(), row.failedCount(),
                row.skippedCount(), row.billedUnits(), row.attempts(), row.lastError(), row.createdAt(),
                row.finishedAt());
    }
}
