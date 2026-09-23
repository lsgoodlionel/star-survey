package cn.mjy.platform.delivery;

import java.util.Arrays;
import java.util.Optional;

/** 触达渠道。渠道决定用哪个 {@link DeliveryProvider}、退订按哪条名单查、地址长什么样。 */
public enum DeliveryChannel {

    EMAIL("email"),
    SMS("sms");

    private final String code;

    DeliveryChannel(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<DeliveryChannel> fromCode(String code) {
        return Arrays.stream(values()).filter(channel -> channel.code.equals(code)).findFirst();
    }

    public static DeliveryChannel require(String code) {
        return fromCode(code).orElseThrow(() -> new IllegalArgumentException("unknown delivery channel: " + code));
    }
}
