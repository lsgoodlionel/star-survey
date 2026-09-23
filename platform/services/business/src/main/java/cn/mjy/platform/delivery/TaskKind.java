package cn.mjy.platform.delivery;

import java.util.Arrays;

/** 任务种类：首邀、催答、新答卷通知。三者共用同一套发送与回执路径，只在筛选收件人时不同。 */
public enum TaskKind {

    INVITE("invite"),
    REMINDER("reminder"),
    NOTIFICATION("notification");

    private final String code;

    TaskKind(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static TaskKind fromCode(String code) {
        return Arrays.stream(values()).filter(kind -> kind.code.equals(code)).findFirst()
                .orElseThrow(() -> new IllegalStateException("unknown task kind in database: " + code));
    }
}
