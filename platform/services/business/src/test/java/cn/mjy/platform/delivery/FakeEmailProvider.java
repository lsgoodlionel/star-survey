package cn.mjy.platform.delivery;

import org.springframework.stereotype.Component;

/** 测试用邮件渠道商。 */
@Component
class FakeEmailProvider extends FakeDeliveryProvider {

    @Override
    public DeliveryChannel channel() {
        return DeliveryChannel.EMAIL;
    }

    @Override
    public String name() {
        return "fake-email";
    }
}
