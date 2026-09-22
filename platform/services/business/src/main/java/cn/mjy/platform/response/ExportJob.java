package cn.mjy.platform.response;

import java.time.Instant;
import java.util.UUID;

/** 作业行（response_export_job）的只读快照，仅在本模块内部流转；对外用 {@link ExportJobView}。 */
record ExportJob(
        UUID id,
        UUID surveyId,
        String requestedBy,
        ExportFormat format,
        String templateVersion,
        String filterJson,
        String planJson,
        boolean revealSensitive,
        String idempotencyKey,
        int batchSize,
        ExportStatus status,
        boolean snapshotDone,
        Integer snapshotVersion,
        String snapshotGeneration,
        Long snapshotResponseId,
        Long totalRows,
        long nextSeq,
        int attempts,
        UUID leaseToken,
        String lastError,
        String fileKey,
        Long fileSize,
        String fileSha256,
        Instant createdAt,
        Instant startedAt,
        Instant finishedAt,
        Instant expiresAt) {

    long processedRows() {
        return nextSeq - 1;
    }
}
