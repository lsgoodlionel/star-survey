package cn.mjy.platform.engine;

import java.time.Instant;
import java.util.UUID;

/**
 * 答卷投影的只读视图。
 *
 * @param completedAt 引擎判定完成的时刻；未完成为 {@code null}
 * @param deletedAt   删除时刻；未删除为 {@code null}
 */
public record ResponseProjection(
        ResponseKey key,
        ResponseState state,
        Instant firstEventAt,
        Instant completedAt,
        Instant deletedAt,
        UUID lastEventId) {
}
