package cn.mjy.platform.delivery;

import java.util.Arrays;

/** 任务状态。queued/running 是"在途"，其余为终态。 */
public enum TaskStatus {

    QUEUED("queued"),
    RUNNING("running"),
    COMPLETED("completed"),
    CANCELLED("cancelled"),
    FAILED("failed");

    private final String code;

    TaskStatus(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public boolean isOpen() {
        return this == QUEUED || this == RUNNING;
    }

    public static TaskStatus fromCode(String code) {
        return Arrays.stream(values()).filter(status -> status.code.equals(code)).findFirst()
                .orElseThrow(() -> new IllegalStateException("unknown task status in database: " + code));
    }
}
