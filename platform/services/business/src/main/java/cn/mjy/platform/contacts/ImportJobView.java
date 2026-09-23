package cn.mjy.platform.contacts;

import java.util.UUID;

/** 导入作业进度与逐类计数。processedRows 是已判定的行数（断点之前的全部行）。 */
public record ImportJobView(UUID jobId, UUID listId, String status, int totalRows, int processedRows,
        int created, int updated, int duplicate, int invalid) {
}
