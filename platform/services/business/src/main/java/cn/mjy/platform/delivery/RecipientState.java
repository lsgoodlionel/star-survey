package cn.mjy.platform.delivery;

import java.util.Arrays;

/**
 * 单个收件人的投递状态。
 *
 * <p>{@link #SENDING} 是"已认领、结果未知"：认领与实际发送分处两个事务，进程在中间死掉就停在这里。
 * 恢复时用台账里同一个幂等键重发，渠道商据此只投递一次，所以这个状态不会造成重复发送。
 */
public enum RecipientState {

    QUEUED("queued"),
    SENDING("sending"),
    SENT("sent"),
    FAILED("failed"),
    BOUNCED("bounced"),
    UNSUBSCRIBED("unsubscribed"),
    SKIPPED("skipped");

    private final String code;

    RecipientState(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public boolean isPending() {
        return this == QUEUED || this == SENDING;
    }

    public static RecipientState fromCode(String code) {
        return Arrays.stream(values()).filter(state -> state.code.equals(code)).findFirst()
                .orElseThrow(() -> new IllegalStateException("unknown recipient state in database: " + code));
    }
}
