package cn.mjy.platform.delivery;

/**
 * 一次投递请求的即时结果。真正的投递结局由回执给出（见 {@link ReceiptService}）：
 * {@link Status#ACCEPTED} 只表示渠道商收下了。
 *
 * @param errorCode 失败原因的机器可读码；不得包含收件地址
 */
public record SendResult(Status status, String providerMessageId, String errorCode) {

    /** RETRY：暂时性失败（限流、网络），可原样重试；REJECTED：永久失败（地址非法、被拒），不再重试。 */
    public enum Status { ACCEPTED, REJECTED, RETRY }

    public static SendResult accepted(String providerMessageId) {
        return new SendResult(Status.ACCEPTED, providerMessageId, null);
    }

    public static SendResult rejected(String errorCode) {
        return new SendResult(Status.REJECTED, null, errorCode);
    }

    public static SendResult retry(String errorCode) {
        return new SendResult(Status.RETRY, null, errorCode);
    }
}
