package cn.mjy.platform.delivery;

/** 400：请求本身不合法（参数名非法、名单为空、地址形状不对等）。消息里不得出现收件地址。 */
public class InvalidDeliveryRequestException extends RuntimeException {

    public InvalidDeliveryRequestException(String message) {
        super(message);
    }
}
