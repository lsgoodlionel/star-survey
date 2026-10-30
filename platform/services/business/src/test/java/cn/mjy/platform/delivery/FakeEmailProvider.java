package cn.mjy.platform.delivery;

import org.springframework.stereotype.Component;

/** 测试用邮件渠道商。 */
@Component
class FakeEmailProvider extends FakeDeliveryProvider {

    private volatile boolean available = true;

    @Override
    public DeliveryChannel channel() {
        return DeliveryChannel.EMAIL;
    }

    @Override
    public String name() {
        return "fake-email";
    }

    @Override
    public boolean available(cn.mjy.platform.shared.TenantId tenant) {
        return available;
    }

    void available(boolean value) {
        available = value;
    }
}
