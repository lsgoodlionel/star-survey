package cn.mjy.platform.response;

import java.time.Instant;
import java.util.Map;

/**
 * 明细列表中的一行：投影（状态与时间）＋版本标签＋作答值。
 *
 * @param version       该答卷所属的已发布版本号（按 (实例, sid) 归入）
 * @param startedAt     平台收到该答卷第一个事件的时刻
 * @param answersStatus 作答值是否附上；非 {@link AnswersStatus#AVAILABLE} 时 {@code answers} 为 {@code null}
 * @param answers       引擎列名 → 值（敏感列可能已遮蔽）；列含义见字段字典
 */
public record ResponseRow(
        int version,
        String engineInstanceId,
        long engineSid,
        String generation,
        long responseId,
        String state,
        Instant startedAt,
        Instant completedAt,
        Instant deletedAt,
        AnswersStatus answersStatus,
        Map<String, String> answers) {
}
