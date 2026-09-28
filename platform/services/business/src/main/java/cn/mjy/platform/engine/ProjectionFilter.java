package cn.mjy.platform.engine;

import java.time.Instant;
import java.time.OffsetDateTime;

/**
 * 投影行的筛选条件：状态、首事件时间区间、完成时间区间，以及导出用的快照水位线。
 *
 * <p>所有条件都在 SQL 里求值（{@link ResponseProjectionQuery#page}），键集翻页的排序与游标不受影响：
 * 加条件只是让扫描跳过不命中的行，不改变 {@code ORDER BY generation, response_id} 这条顺序，
 * 因此"翻页期间已存在的行不重复、不遗漏"的理由与无条件时一字不差（ADR 0013 决定 1）。
 *
 * <p>时间区间是闭区间，{@code null} 表示该侧不限。{@code completedFrom/To} 只会命中已完成的行：
 * 未完成的行 {@code completed_at} 为空，任何区间都不命中。
 */
public record ProjectionFilter(
        ResponseState state,
        Instant startedFrom,
        Instant startedTo,
        Instant completedFrom,
        Instant completedTo,
        Instant createdAtOrBefore) {

    /** 不带任何条件。 */
    public static final ProjectionFilter ALL = new ProjectionFilter(null, null, null, null, null, null);

    public static ProjectionFilter ofState(ResponseState state) {
        return ALL.withState(state);
    }

    public ProjectionFilter withState(ResponseState value) {
        return new ProjectionFilter(value, startedFrom, startedTo, completedFrom, completedTo, createdAtOrBefore);
    }

    public ProjectionFilter withStarted(Instant from, Instant to) {
        return new ProjectionFilter(state, from, to, completedFrom, completedTo, createdAtOrBefore);
    }

    public ProjectionFilter withCompleted(Instant from, Instant to) {
        return new ProjectionFilter(state, startedFrom, startedTo, from, to, createdAtOrBefore);
    }

    /** 快照水位线：只要平台首次写入投影不晚于该时刻的行（答卷导出，ADR 0015）。 */
    public ProjectionFilter withCreatedAtOrBefore(OffsetDateTime watermark) {
        return new ProjectionFilter(state, startedFrom, startedTo, completedFrom, completedTo,
                watermark == null ? null : watermark.toInstant());
    }
}
