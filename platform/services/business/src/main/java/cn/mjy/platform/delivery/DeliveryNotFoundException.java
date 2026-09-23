package cn.mjy.platform.delivery;

/** 404：链接、任务、规则不存在，或属于别的租户。消息里不得出现收件地址。 */
public class DeliveryNotFoundException extends RuntimeException {

    public DeliveryNotFoundException(String message) {
        super(message);
    }
}
