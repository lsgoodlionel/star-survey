package cn.mjy.platform.response;

import java.time.Instant;

/** 物化快照里的一行：导出顺序号＋答卷键＋物化那一刻的状态与时间。 */
record ExportItem(long seq, int version, String engineInstanceId, long engineSid, String generation,
        long responseId, String state, Instant firstEventAt, Instant completedAt) {
}
