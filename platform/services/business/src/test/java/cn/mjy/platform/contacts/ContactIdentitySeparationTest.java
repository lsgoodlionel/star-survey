package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 三类业务身份互不合并（WP-18 切片 18.1，R18-01 / R18-04 / R18-08，ADR 0017）。
 * 验收："不同应用同 openid 不合并"。
 */
@SpringBootTest
class ContactIdentitySeparationTest {

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    private static final String OPEN_ID = "oA1b2C3d4E5f6G7h8";

    @Test
    void theSameOpenIdUnderTwoAppsStaysTwoContacts() {
        TenantContext owner = fixture.newTenant();

        ContactView appOne = contacts.create(owner, null, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withIdentity(new ExternalContactIdentity("wecom", "app-1", OPEN_ID)));
        ContactView appTwo = contacts.create(owner, null, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withIdentity(new ExternalContactIdentity("wecom", "app-2", OPEN_ID)));

        assertThat(appOne.id()).isNotEqualTo(appTwo.id());
        assertThat(contacts.search(owner, ContactQuery.all()).items()).hasSize(2);
    }

    @Test
    void theSameOpenIdUnderTheSameAppIsTheSameDirectoryContact() {
        TenantContext owner = fixture.newTenant();
        ExternalContactIdentity identity = new ExternalContactIdentity("dingtalk", "corp-1", OPEN_ID);

        ContactView first = contacts.create(owner, null, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withIdentity(identity));
        ContactView again = contacts.create(owner, null, ContactKind.RESPONDENT,
                ContactDraft.named("甲改名").withIdentity(identity));

        assertThat(again.id()).isEqualTo(first.id());
        assertThat(again.displayName()).isEqualTo("甲改名");
        assertThat(contacts.search(owner, ContactQuery.all()).items()).hasSize(1);
    }

    @Test
    void theSameOpenIdUnderTwoKindsStaysTwoContacts() {
        TenantContext owner = fixture.newTenant();
        ExternalContactIdentity identity = new ExternalContactIdentity("feishu", "corp-9", OPEN_ID);

        ContactView member = contacts.create(owner, null, ContactKind.ORG_MEMBER,
                ContactDraft.named("甲").withIdentity(identity));
        ContactView respondent = contacts.create(owner, null, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withIdentity(identity));

        assertThat(member.id()).isNotEqualTo(respondent.id());
    }

    @Test
    void thesamePersonAsStaffAndAsRespondentStaysSeparate() {
        TenantContext owner = fixture.newTenant();
        UUID staffList = fixture.list(owner, "员工名册", ContactKind.STAFF, DedupeKey.EMAIL).id();
        UUID audience = fixture.list(owner, "答卷受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();

        ContactView staff = contacts.create(owner, staffList, ContactKind.STAFF,
                ContactDraft.named("李四").withEmail("li@example.com"));
        ContactView respondent = contacts.create(owner, audience, ContactKind.RESPONDENT,
                ContactDraft.named("李四").withEmail("li@example.com"));

        assertThat(staff.id()).isNotEqualTo(respondent.id());
        assertThat(staff.kind()).isEqualTo(ContactKind.STAFF);
        assertThat(respondent.kind()).isEqualTo(ContactKind.RESPONDENT);
        assertThat(contacts.search(owner, ContactQuery.all().inList(staffList)).items()).hasSize(1);
        assertThat(contacts.search(owner, ContactQuery.all().inList(audience)).items()).hasSize(1);
    }

    @Test
    void aContactMustMatchTheKindOfItsList() {
        TenantContext owner = fixture.newTenant();
        UUID staffList = fixture.list(owner, "员工名册", ContactKind.STAFF, DedupeKey.EMAIL).id();

        assertThatThrownBy(() -> contacts.create(owner, staffList, ContactKind.RESPONDENT,
                ContactDraft.named("李四").withEmail("li@example.com")))
                .isInstanceOf(InvalidContactRequestException.class)
                .hasMessageContaining("kind");
    }

    @Test
    void linkingIsExplicitAuditedAndNeverMerges() {
        TenantContext owner = fixture.newTenant();
        UUID staffList = fixture.list(owner, "员工名册", ContactKind.STAFF, DedupeKey.EMAIL).id();
        UUID audience = fixture.list(owner, "答卷受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView staff = contacts.create(owner, staffList, ContactKind.STAFF,
                ContactDraft.named("王五").withEmail("wang@example.com"));
        ContactView respondent = contacts.create(owner, audience, ContactKind.RESPONDENT,
                ContactDraft.named("王五").withEmail("wang@example.com"));

        ContactLinkView link = contacts.link(owner, staff.id(), respondent.id(), "同一人：员工兼受访者");

        // 两行都还在，各自保留 id 与所属名单——关联不是合并。
        assertThat(contacts.get(owner, staff.id()).listId()).isEqualTo(staffList);
        assertThat(contacts.get(owner, respondent.id()).listId()).isEqualTo(audience);
        List<ContactLinkView> links = contacts.links(owner, staff.id());
        assertThat(links).hasSize(1);
        assertThat(links.getFirst().id()).isEqualTo(link.id());
        assertThat(links.getFirst().reason()).isEqualTo("同一人：员工兼受访者");
    }

    @Test
    void linkingTheSamePairTwiceIsIdempotentAndOrderDoesNotMatter() {
        TenantContext owner = fixture.newTenant();
        UUID staffList = fixture.list(owner, "员工名册", ContactKind.STAFF, DedupeKey.EMAIL).id();
        UUID audience = fixture.list(owner, "答卷受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView a = contacts.create(owner, staffList, ContactKind.STAFF,
                ContactDraft.named("赵六").withEmail("zhao@example.com"));
        ContactView b = contacts.create(owner, audience, ContactKind.RESPONDENT,
                ContactDraft.named("赵六").withEmail("zhao@example.com"));

        ContactLinkView first = contacts.link(owner, a.id(), b.id(), "同一人");
        ContactLinkView again = contacts.link(owner, b.id(), a.id(), "同一人");

        assertThat(again.id()).isEqualTo(first.id());
        assertThat(contacts.links(owner, a.id())).hasSize(1);
    }

    @Test
    void unlinkingLeavesBothContactsAndAllowsRelinking() {
        TenantContext owner = fixture.newTenant();
        UUID staffList = fixture.list(owner, "员工名册", ContactKind.STAFF, DedupeKey.EMAIL).id();
        UUID audience = fixture.list(owner, "答卷受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView a = contacts.create(owner, staffList, ContactKind.STAFF,
                ContactDraft.named("孙七").withEmail("sun@example.com"));
        ContactView b = contacts.create(owner, audience, ContactKind.RESPONDENT,
                ContactDraft.named("孙七").withEmail("sun@example.com"));
        ContactLinkView link = contacts.link(owner, a.id(), b.id(), "同一人");

        contacts.unlink(owner, link.id());

        assertThat(contacts.links(owner, a.id())).isEmpty();
        assertThat(contacts.get(owner, a.id()).id()).isEqualTo(a.id());
        assertThat(contacts.get(owner, b.id()).id()).isEqualTo(b.id());
        ContactLinkView relinked = contacts.link(owner, a.id(), b.id(), "再次确认");
        assertThat(relinked.id()).isNotEqualTo(link.id());
    }

    @Test
    void aContactCannotBeLinkedToItself() {
        TenantContext owner = fixture.newTenant();
        UUID audience = fixture.list(owner, "答卷受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView a = contacts.create(owner, audience, ContactKind.RESPONDENT,
                ContactDraft.named("周八").withEmail("zhou@example.com"));

        assertThatThrownBy(() -> contacts.link(owner, a.id(), a.id(), "自己"))
                .isInstanceOf(InvalidContactRequestException.class);
    }
}
