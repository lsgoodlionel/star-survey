package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 标签（WP-18 切片 18.2，R18-02"标签权限隔离"）：每租户一套，跨租户不可见、不可引用。 */
@SpringBootTest
class ContactTagTest {

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private ContactTagService tags;

    @Test
    void tagsAreScopedPerTenantAndTheSameNameMayExistInBoth() {
        TenantContext a = fixture.newTenant();
        TenantContext b = fixture.newTenant();

        ContactTagView inA = tags.create(a, "VIP");
        ContactTagView inB = tags.create(b, "VIP");

        assertThat(inA.id()).isNotEqualTo(inB.id());
        assertThat(tags.list(a)).extracting(ContactTagView::id).containsExactly(inA.id());
        assertThat(tags.list(b)).extracting(ContactTagView::id).containsExactly(inB.id());
    }

    @Test
    void anotherTenantsTagCannotBeAssigned() {
        TenantContext a = fixture.newTenant();
        TenantContext b = fixture.newTenant();
        ContactTagView tagOfB = tags.create(b, "VIP");
        UUID list = fixture.list(a, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView contact = contacts.create(a, list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com"));

        assertThatThrownBy(() -> tags.assign(a, contact.id(), tagOfB.id()))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void tagNamesAreUniquePerTenantIgnoringCase() {
        TenantContext a = fixture.newTenant();
        tags.create(a, "VIP");

        assertThat(tags.create(a, "vip").name()).isEqualTo("VIP");
        assertThat(tags.list(a)).hasSize(1);
    }

    @Test
    void searchByTagReturnsOnlyTaggedContacts() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView tagged = contacts.create(owner, list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com"));
        contacts.create(owner, list, ContactKind.RESPONDENT,
                ContactDraft.named("乙").withEmail("bob@example.com"));
        ContactTagView vip = tags.create(owner, "VIP");
        tags.assign(owner, tagged.id(), vip.id());

        assertThat(contacts.search(owner, ContactQuery.all().withTag("VIP")).items())
                .extracting(ContactView::id).containsExactly(tagged.id());
        assertThat(contacts.get(owner, tagged.id()).tags()).containsExactly("VIP");
    }

    @Test
    void assigningTheSameTagTwiceIsIdempotentAndUnassigningRemovesIt() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ContactView contact = contacts.create(owner, list, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com"));
        ContactTagView vip = tags.create(owner, "VIP");

        tags.assign(owner, contact.id(), vip.id());
        tags.assign(owner, contact.id(), vip.id());
        assertThat(contacts.get(owner, contact.id()).tags()).containsExactly("VIP");

        tags.unassign(owner, contact.id(), vip.id());
        assertThat(contacts.get(owner, contact.id()).tags()).isEmpty();
    }

    @Test
    void aDepartmentAdminCannotTagAContactOutsideItsDepartment() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "员工", ContactKind.STAFF, DedupeKey.EMPLOYEE_ID).id();
        UUID deptA = fixture.unit(owner, null, "A 部").id();
        UUID deptB = fixture.unit(owner, null, "B 部").id();
        ContactView inB = contacts.create(owner, list, ContactKind.STAFF,
                ContactDraft.named("乙").withEmployeeId("B-1").withUnit(deptB));
        ContactTagView vip = tags.create(owner, "VIP");
        TenantContext adminA = fixture.departmentAdmin(owner, "admin-a", deptA);

        assertThatThrownBy(() -> tags.assign(adminA, inB.id(), vip.id()))
                .isInstanceOf(ContactNotFoundException.class);
    }
}
