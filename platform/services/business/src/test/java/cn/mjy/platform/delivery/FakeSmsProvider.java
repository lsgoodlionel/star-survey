package cn.mjy.platform.delivery;

import org.springframework.stereotype.Component;

/** 测试用短信渠道商。 */
@Component
class FakeSmsProvider extends FakeDeliveryProvider {

    @Override
    public DeliveryChannel channel() {
        return DeliveryChannel.SMS;
    }

    @Override
    public String name() {
        return "fake-sms";
    }
}
