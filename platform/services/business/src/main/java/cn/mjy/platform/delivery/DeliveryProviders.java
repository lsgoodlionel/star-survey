package cn.mjy.platform.delivery;

import java.util.EnumMap;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

/**
 * 渠道商注册表：每个渠道最多一个 {@link DeliveryProvider}。
 *
 * <p>没有配置渠道商时不降级、不假装发送：任务直接以"渠道未配置"失败（失败即关闭）。
 * 接入真实服务商就是加一个 bean，其余代码不动。
 */
@Component
class DeliveryProviders {

    private static final Pattern NAME = Pattern.compile("[a-z0-9][a-z0-9-]{0,63}");

    private final Map<DeliveryChannel, DeliveryProvider> byChannel = new EnumMap<>(DeliveryChannel.class);

    DeliveryProviders(List<DeliveryProvider> providers) {
        Map<DeliveryChannel, String> names = new HashMap<>();
        for (DeliveryProvider provider : providers) {
            if (!NAME.matcher(provider.name()).matches()) {
                throw new IllegalStateException("illegal delivery provider name: " + provider.name());
            }
            String existing = names.put(provider.channel(), provider.name());
            if (existing != null) {
                throw new IllegalStateException("two providers registered for channel "
                        + provider.channel().code() + ": " + existing + " and " + provider.name());
            }
            byChannel.put(provider.channel(), provider);
        }
    }

    Optional<DeliveryProvider> find(DeliveryChannel channel) {
        return Optional.ofNullable(byChannel.get(channel));
    }

    DeliveryProvider require(DeliveryChannel channel) {
        return find(channel).orElseThrow(() -> new DeliveryUnavailableException(
                "no delivery provider is configured for channel " + channel.code()));
    }
}
