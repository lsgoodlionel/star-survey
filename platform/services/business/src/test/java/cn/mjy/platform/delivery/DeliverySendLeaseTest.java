package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.ResponseFixture.Published;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.TestPropertySource;

/**
 * 发送租约：认领之后、结果未知的收件人，在租约到期之前<b>不会</b>被重新认领。
 *
 * <p>没有这条约束，"崩溃后恢复"就和"另一个副本此刻正在发"分不开：多副本部署下每一次正常发送都会
 * 在网络往返期间被另一个副本重新认领，于是同一条消息被投递两次（只靠渠道商幂等兜着）。
 */
@DeliveryIntegrationTest
@TestPropertySource(properties = "platform.delivery.send-lease=PT10M")
class DeliverySendLeaseTest {

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private FakeEmailProvider email;

    private Published p;

    @BeforeEach
    void publishedSurvey() {
        email.reset();
        p = fixture.publishedSurvey();
    }

    @Test
    void aRecipientWhoseSendIsStillInFlightIsNotPickedUpAgainWhileTheLeaseHolds() {
        TaskView task = fixture.emailTask(p, 1);
        email.crashAfter(0);
        assertThatThrownBy(() -> fixture.run(p.tenant(), task.id()))
                .isInstanceOf(FakeDeliveryProvider.SimulatedCrash.class);
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .singleElement()
                .satisfies(recipient -> assertThat(recipient.state()).isEqualTo("sending"));

        email.stopCrashing();
        int handled = fixture.run(p.tenant(), task.id());

        // 租约未到期：这一轮一个人都不碰，渠道商也没有被再调用一次。
        assertThat(handled).isZero();
        assertThat(email.callCount()).isEqualTo(1);
        assertThat(email.delivered()).isEmpty();
        // 收件人也没有被丢掉：任务仍在途，租约过期后会被接着做。
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("queued");
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .singleElement()
                .satisfies(recipient -> assertThat(recipient.state()).isEqualTo("sending"));
    }
}
