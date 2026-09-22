package cn.mjy.platform.response;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Arrays;
import java.util.Locale;

/** 导出作业状态：queued → running → completed | failed | cancelled，到期后 expired（ADR 0015）。 */
public enum ExportStatus {
    QUEUED,
    RUNNING,
    COMPLETED,
    FAILED,
    CANCELLED,
    EXPIRED;

    @JsonValue
    public String wire() {
        return name().toLowerCase(Locale.ROOT);
    }

    boolean isOpen() {
        return this == QUEUED || this == RUNNING;
    }

    static ExportStatus fromDb(String value) {
        return Arrays.stream(values()).filter(s -> s.wire().equals(value)).findFirst()
                .orElseThrow(() -> new IllegalStateException("unknown export status " + value));
    }
}
