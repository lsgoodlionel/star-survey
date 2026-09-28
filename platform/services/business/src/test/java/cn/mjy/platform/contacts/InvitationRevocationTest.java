package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.FakePublishGateway;
import cn.mjy.platform.survey.gateway.GatewayRevokeRequest;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 邀请码撤销必须在<b>引擎侧</b>生效（ADR 0016 缺口「邀请码撤销的平台接口」）。
 *
 * <p>这里钉住的是那个缺口本身：撤销曾经只写平台自己的 {@code revoked_at}，
 * 引擎 {@code tokens_<sid>} 里那一行原封不动，于是<b>被撤销的令牌照样能打开问卷</b>，
 * 而且平台显示"已撤销"，没有任何人会再发现。
 *
 * <p>因此三条断言缺一不可：
 * <ol>
 *   <li>撤销<b>确实调了</b>引擎（不是只改了平台自己的一行）；</li>
 *   <li>调的是<b>这条映射自己</b>的实例、sid 与令牌（旧版本的码要在旧 sid 上删，ADR 0012）；</li>
 *   <li>引擎没确认时<b>不写</b> {@code revoked_at}——宁可撤销失败，也不要平台记着"已撤销"
 *       而令牌还活着。</li>
 * </ol>
 */
@SpringBootTest
class InvitationRevocationTest {

    @Autowired
    private ResponseFixture responses;

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private ContactParticipationService participations;

    @Autowired
    private FakePublishGateway gateway;

    private record World(TenantContext owner, UUID surveyId, ContactView contact) {
    }

    private World world() {
        Published published = responses.publishedSurvey();
        TenantContext owner = published.owner();
        UUID list = fixture.list(owner, "受众 " + UUID.randomUUID(), ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView contact = contacts.create(owner, list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail(UUID.randomUUID() + "@example.com"));
        return new World(owner, published.surveyId(), contact);
    }

    private List<GatewayRevokeRequest> callsFor(String token) {
        return gateway.revokeCalls().stream().filter(call -> call.participantToken().equals(token)).toList();
    }

    @Test
    void revokingDeletesTheParticipantInTheEngine() {
        World w = world();
        String token = "tok-engine-" + UUID.randomUUID().toString().substring(0, 8);
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), token);

        participations.revoke(w.owner(), link.id());

        assertThat(callsFor(token))
                .as("撤销必须让引擎删掉那一行参与者，否则令牌照样能打开问卷")
                .hasSize(1);
        assertThat(participations.current(w.owner(), w.contact().id(), w.surveyId())).isEmpty();
    }

    @Test
    void theEngineCallNamesTheSurveyThatIssuedTheToken() {
        World w = world();
        String token = "tok-target-" + UUID.randomUUID().toString().substring(0, 8);
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), token);

        participations.revoke(w.owner(), link.id());

        GatewayRevokeRequest call = callsFor(token).getFirst();
        assertThat(call.engineInstanceId()).isEqualTo(link.engineInstanceId());
        assertThat(call.surveyId()).isEqualTo(link.engineSid());
        assertThat(call.participantToken()).isEqualTo(token);
    }

    @Test
    void anUnconfirmedRevocationLeavesTheMappingActive() {
        World w = world();
        String token = "tok-stuck-" + UUID.randomUUID().toString().substring(0, 8);
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), token);
        gateway.failRevoke(token);

        try {
            assertThatThrownBy(() -> participations.revoke(w.owner(), link.id()))
                    .isInstanceOf(InvitationRevocationFailedException.class);

            assertThat(participations.current(w.owner(), w.contact().id(), w.surveyId()))
                    .as("引擎没删成功却记成已撤销，就是「平台以为撤销了，令牌照样能进」")
                    .get().extracting(ParticipationView::participantToken).isEqualTo(token);
        } finally {
            gateway.allowRevoke(token);
        }
    }

    @Test
    void retryingAfterAFailureStillRevokes() {
        World w = world();
        String token = "tok-retry-" + UUID.randomUUID().toString().substring(0, 8);
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), token);
        gateway.failRevoke(token);
        assertThatThrownBy(() -> participations.revoke(w.owner(), link.id()))
                .isInstanceOf(InvitationRevocationFailedException.class);

        gateway.allowRevoke(token);
        participations.revoke(w.owner(), link.id());

        assertThat(callsFor(token)).hasSize(2);
        assertThat(participations.current(w.owner(), w.contact().id(), w.surveyId())).isEmpty();
    }

    /** 引擎里本来就没有这个码（幂等重试，或有人已在引擎侧删过）：结论同样是"进不去"。 */
    @Test
    void aTokenTheEngineNoLongerHasCountsAsRevoked() {
        World w = world();
        String token = "tok-absent-" + UUID.randomUUID().toString().substring(0, 8);
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), token);
        gateway.revokeFindsNothing(token);

        participations.revoke(w.owner(), link.id());

        assertThat(participations.current(w.owner(), w.contact().id(), w.surveyId())).isEmpty();
    }

    /** 越出数据范围的人连"这条映射存不存在"都不该知道，更不该触发一次引擎删除。 */
    @Test
    void anotherTenantCannotRevokeAndNeverReachesTheEngine() {
        World w = world();
        String token = "tok-cross-" + UUID.randomUUID().toString().substring(0, 8);
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), token);
        TenantContext otherOwner = fixture.newTenant();

        assertThatThrownBy(() -> participations.revoke(otherOwner, link.id()))
                .isInstanceOf(ContactNotFoundException.class);

        assertThat(callsFor(token)).isEmpty();
    }
}
