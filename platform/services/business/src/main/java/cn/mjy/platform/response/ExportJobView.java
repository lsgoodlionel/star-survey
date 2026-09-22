package cn.mjy.platform.response;

import cn.mjy.platform.response.ExportRequest.ExportFilter;
import java.time.Instant;
import java.util.UUID;

/**
 * 导出作业的对外视图（状态与进度）。不含任何作答数据，也不含文件在存储里的位置。
 *
 * @param totalRows     快照物化完成后的总行数；物化前为 {@code null}
 * @param processedRows 已写入分片的行数
 * @param error         最近一次失败的原因码（{@code answers_unavailable}、{@code permission_changed} 等）
 */
public record ExportJobView(
        UUID jobId,
        UUID surveyId,
        String format,
        String templateVersion,
        ExportFilter filter,
        String status,
        boolean sensitiveRevealed,
        Long totalRows,
        long processedRows,
        int attempts,
        String error,
        Long fileSize,
        String sha256,
        Instant createdAt,
        Instant startedAt,
        Instant finishedAt,
        Instant expiresAt) {
}
