package cn.mjy.platform.engine;

import java.util.Arrays;
import java.util.Optional;

/**
 * 答卷投影状态。按 {@code rank} 单调前进：只接受比当前更靠后的状态，
 * 因此迟到或乱序的事件不会让状态倒退。{@link #DELETED} 为终态——同一代次内答卷号不会复用
 * （MariaDB 自增值持久化、PostgreSQL 序列不回收），删除后再来的任何事件都只是迟到的旧事实。
 */
public enum ResponseState {

    IN_PROGRESS("in_progress", 1, null),
    ENGINE_COMPLETED("engine_completed", 2, "response.engine_completed"),
    DELETED("deleted", 3, "response.deleted");

    private final String dbValue;
    private final int rank;
    private final String outboxEventType;

    ResponseState(String dbValue, int rank, String outboxEventType) {
        this.dbValue = dbValue;
        this.rank = rank;
        this.outboxEventType = outboxEventType;
    }

    public String dbValue() {
        return dbValue;
    }

    public boolean isAfter(ResponseState other) {
        return rank > other.rank;
    }

    /** 进入该状态时要通知下游的发件箱事件类型；进行中不通知（保存过于频繁，下游也不需要）。 */
    public Optional<String> outboxEventType() {
        return Optional.ofNullable(outboxEventType);
    }

    public static ResponseState fromDb(String value) {
        return Arrays.stream(values())
                .filter(state -> state.dbValue.equals(value))
                .findFirst()
                .orElseThrow(() -> new IllegalStateException("unknown projection state in database: " + value));
    }
}
