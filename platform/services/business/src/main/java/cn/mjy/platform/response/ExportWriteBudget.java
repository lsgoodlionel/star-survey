package cn.mjy.platform.response;

import java.time.Duration;

/** 收尾阶段的协作式时间与最终制品字节预算。 */
final class ExportWriteBudget {
    private final long maxBytes;
    private final long deadlineNanos;

    ExportWriteBudget(long maxBytes, Duration timeout) {
        this.maxBytes = maxBytes;
        long nanos = timeout.toNanos();
        long now = System.nanoTime();
        deadlineNanos = nanos >= Long.MAX_VALUE - now ? Long.MAX_VALUE : now + nanos;
    }

    static ExportWriteBudget unlimited() {
        return new ExportWriteBudget(Long.MAX_VALUE, Duration.ofNanos(Long.MAX_VALUE));
    }

    void checkpoint() {
        if (System.nanoTime() - deadlineNanos >= 0) {
            throw new ExportBudgetExceededException("导出制品生成超过允许时长");
        }
    }

    void beforeWrite(long written, int additional) {
        checkpoint();
        if (additional < 0 || written > maxBytes - additional) {
            throw new ExportBudgetExceededException("导出制品超过允许的最大字节数");
        }
    }
}
