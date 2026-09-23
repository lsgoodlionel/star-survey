package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 联系人 ↔ 引擎参与者令牌（WP-18 切片 18.1 的投放接点，ADR 0016 邀请码 / 引擎 tokens_&lt;sid&gt;）。
 * 平台只记录映射，不在此发码也不发送；投放车道读这份映射去发。
 */
@SpringBootTest
class ContactParticipationTest {

    @Autowired
    private ResponseFixture responses;

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private ContactParticipationService participations;

    @Autowired
    private MemberService members;

    private record World(TenantContext owner, UUID surveyId, ContactView contact) {
    }

    private World world() {
        Published published = responses.publishedSurvey();
        TenantContext owner = published.owner();
        UUID list = fixture.list(owner, "受众 " + UUID.randomUUID(), ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView contact = contacts.create(owner, list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com"));
        return new World(owner, published.surveyId(), contact);
    }

    @Test
    void aContactIsMappedToTheLivePublishedVersionsToken() {
        World w = world();

        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-abc-001");

        assertThat(link.contactId()).isEqualTo(w.contact().id());
        assertThat(link.versionNo()).isEqualTo(1);
        assertThat(link.engineSid()).isPositive();
        assertThat(participations.current(w.owner(), w.contact().id(), w.surveyId()))
                .get().extracting(ParticipationView::participantToken).isEqualTo("tok-abc-001");
    }

    @Test
    void mappingTheSameContactAndTokenAgainIsIdempotent() {
        World w = world();

        ParticipationView first = participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-abc-002");
        ParticipationView again = participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-abc-002");

        assertThat(again.id()).isEqualTo(first.id());
    }

    @Test
    void aTokenIsNeverSharedBetweenTwoContacts() {
        World w = world();
        ContactView other = contacts.create(w.owner(), contacts.get(w.owner(), w.contact().id()).listId(),
                ContactKind.RESPONDENT, ContactDraft.named("乙").withEmail("bob@example.com"));
        participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-shared-1");

        assertThatThrownBy(() -> participations.link(w.owner(), other.id(), w.surveyId(), "tok-shared-1"))
                .isInstanceOf(ContactConflictException.class);
    }

    @Test
    void aContactGetsOneLiveTokenPerPublishedVersion() {
        World w = world();
        participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-first-1");

        assertThatThrownBy(() -> participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-second-1"))
                .isInstanceOf(ContactConflictException.class);
    }

    @Test
    void revokingReleasesBothTheContactSlotAndTheToken() {
        World w = world();
        ParticipationView link = participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-revoke-1");

        participations.revoke(w.owner(), link.id());

        assertThat(participations.current(w.owner(), w.contact().id(), w.surveyId())).isEmpty();
        ParticipationView reissued = participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-revoke-2");
        assertThat(reissued.id()).isNotEqualTo(link.id());
    }

    @Test
    void anUnpublishedSurveyHasNoParticipantTokens() {
        World w = world();
        UUID unknownSurvey = UUID.randomUUID();

        assertThatThrownBy(() -> participations.link(w.owner(), w.contact().id(), unknownSurvey, "tok-x-0001"))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void anotherTenantCannotMapOrReadTheParticipation() {
        World w = world();
        participations.link(w.owner(), w.contact().id(), w.surveyId(), "tok-cross-1");
        TenantContext otherOwner = fixture.newTenant();

        assertThatThrownBy(() -> participations.current(otherOwner, w.contact().id(), w.surveyId()))
                .isInstanceOf(ContactNotFoundException.class);
        assertThatThrownBy(() -> participations.link(otherOwner, w.contact().id(), w.surveyId(), "tok-cross-2"))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void aDepartedAdminCannotIssueParticipantTokens() {
        World w = world();
        TenantContext admin = fixture.tenantAdmin(w.owner(), "contacts-admin");
        members.removeBySystem(w.owner().tenantId(), "contacts-admin", "trace-departure");

        assertThatThrownBy(() -> participations.link(admin, w.contact().id(), w.surveyId(), "tok-gone-1"))
                .isInstanceOf(ContactAccessDeniedException.class);
    }
}
