package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 组织单元与部门级数据范围（WP-18 切片 18.2，R18-02："树无循环、部门管理员范围正确"）。
 * 验收："离职后立即失权"。
 */
@SpringBootTest
class ContactOrgUnitScopeTest {

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private OrgUnitService units;

    @Autowired
    private ContactScopeService scopes;

    @Autowired
    private MemberService members;

    /** 两个部门，各一个联系人，外加一个不属于任何部门的联系人。 */
    private record World(TenantContext owner, UUID listId, UUID deptA, UUID subA, UUID deptB,
            ContactView inA, ContactView inSubA, ContactView inB, ContactView loose) {
    }

    private World world() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "员工名册", ContactKind.STAFF, DedupeKey.EMPLOYEE_ID).id();
        UUID deptA = fixture.unit(owner, null, "A 部").id();
        UUID subA = fixture.unit(owner, deptA, "A 部一组").id();
        UUID deptB = fixture.unit(owner, null, "B 部").id();
        ContactView inA = contacts.create(owner, list, ContactKind.STAFF,
                ContactDraft.named("甲").withEmployeeId("A-1").withUnit(deptA));
        ContactView inSubA = contacts.create(owner, list, ContactKind.STAFF,
                ContactDraft.named("甲二").withEmployeeId("A-2").withUnit(subA));
        ContactView inB = contacts.create(owner, list, ContactKind.STAFF,
                ContactDraft.named("乙").withEmployeeId("B-1").withUnit(deptB));
        ContactView loose = contacts.create(owner, list, ContactKind.STAFF,
                ContactDraft.named("丙").withEmployeeId("C-1"));
        return new World(owner, list, deptA, subA, deptB, inA, inSubA, inB, loose);
    }

    @Test
    void aDepartmentAdminSeesOnlyItsOwnDepartmentAndDescendants() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());

        assertThat(contacts.search(adminA, ContactQuery.all()).items())
                .extracting(ContactView::id)
                .containsExactlyInAnyOrder(w.inA().id(), w.inSubA().id());
    }

    @Test
    void aDepartmentAdminCannotReadAnotherDepartmentsContact() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());

        assertThatThrownBy(() -> contacts.get(adminA, w.inB().id()))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void aDepartmentAdminCannotWriteIntoAnotherDepartment() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());

        assertThatThrownBy(() -> contacts.create(adminA, w.listId(), ContactKind.STAFF,
                ContactDraft.named("外人").withEmployeeId("X-1").withUnit(w.deptB())))
                .isInstanceOf(ContactNotFoundException.class);
        assertThatThrownBy(() -> contacts.update(adminA, w.inB().id(), ContactDraft.named("改名")))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void contactsWithoutAUnitAreVisibleOnlyToTenantWideAdmins() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());
        TenantContext tenantAdmin = fixture.tenantAdmin(w.owner(), "admin-all");

        assertThatThrownBy(() -> contacts.get(adminA, w.loose().id()))
                .isInstanceOf(ContactNotFoundException.class);
        assertThat(contacts.get(tenantAdmin, w.loose().id()).id()).isEqualTo(w.loose().id());
        assertThat(contacts.search(tenantAdmin, ContactQuery.all()).items()).hasSize(4);
    }

    @Test
    void aMemberWithoutManageMembersSeesNothing() {
        World w = world();
        TenantContext plain = fixture.plainMember(w.owner(), "plain");

        assertThatThrownBy(() -> contacts.search(plain, ContactQuery.all()))
                .isInstanceOf(ContactAccessDeniedException.class);
        assertThatThrownBy(() -> contacts.get(plain, w.inA().id()))
                .isInstanceOf(ContactAccessDeniedException.class);
    }

    @Test
    void movingAUnitOutOfADepartmentTakesItsContactsWithIt() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());
        assertThat(contacts.search(adminA, ContactQuery.all()).items()).hasSize(2);

        units.move(w.owner(), w.subA(), w.deptB());

        // 子部门连人一起离开 A 部的范围：立刻看不到，没有残留。
        assertThat(contacts.search(adminA, ContactQuery.all()).items())
                .extracting(ContactView::id).containsExactly(w.inA().id());
        assertThatThrownBy(() -> contacts.get(adminA, w.inSubA().id()))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void aDepartmentAdminCannotMoveAUnitOutOfItsOwnScope() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());

        assertThatThrownBy(() -> units.move(adminA, w.subA(), w.deptB()))
                .isInstanceOf(ContactNotFoundException.class);
        assertThatThrownBy(() -> units.move(adminA, w.subA(), null))
                .isInstanceOf(ContactAccessDeniedException.class);
    }

    @Test
    void aUnitCannotBecomeItsOwnDescendant() {
        World w = world();

        assertThatThrownBy(() -> units.move(w.owner(), w.deptA(), w.subA()))
                .isInstanceOf(ContactConflictException.class);
        assertThatThrownBy(() -> units.move(w.owner(), w.deptA(), w.deptA()))
                .isInstanceOf(InvalidContactRequestException.class);
    }

    @Test
    void searchCanBeNarrowedToOneUnitWithOrWithoutDescendants() {
        World w = world();

        assertThat(contacts.search(w.owner(), ContactQuery.all().inUnit(w.deptA(), true)).items())
                .extracting(ContactView::id).containsExactlyInAnyOrder(w.inA().id(), w.inSubA().id());
        assertThat(contacts.search(w.owner(), ContactQuery.all().inUnit(w.deptA(), false)).items())
                .extracting(ContactView::id).containsExactly(w.inA().id());
    }

    @Test
    void aDepartedAdminLosesContactAccessImmediately() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());
        assertThat(contacts.search(adminA, ContactQuery.all()).items()).hasSize(2);

        // 组织同步判定离职 → 平台成员被移除（access 模块），通讯录随即失权。
        members.removeBySystem(w.owner().tenantId(), "admin-a", "trace-departure");

        assertThatThrownBy(() -> contacts.search(adminA, ContactQuery.all()))
                .isInstanceOf(ContactAccessDeniedException.class);
    }

    @Test
    void revokingTheScopeGrantNarrowsTheDepartmentAdminBackToNothing() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());
        ContactScopeView grant = scopes.list(w.owner()).stream()
                .filter(s -> s.actorId().equals("admin-a")).findFirst().orElseThrow();

        scopes.revoke(w.owner(), grant.id());

        // 范围行是唯一的放行依据（失败即关闭）：没有范围行就什么都看不到，绝不回退成全租户。
        assertThat(scopes.list(w.owner())).extracting(ContactScopeView::actorId).doesNotContain("admin-a");
        assertThat(contacts.search(adminA, ContactQuery.all()).items()).isEmpty();
        assertThatThrownBy(() -> contacts.get(adminA, w.inA().id()))
                .isInstanceOf(ContactNotFoundException.class);
    }

    @Test
    void aDepartmentAdminCannotWidenItsOwnScope() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());

        assertThatThrownBy(() -> scopes.grant(adminA, "admin-a", w.deptB(), null))
                .isInstanceOf(ContactAccessDeniedException.class);
    }

    @Test
    void disablingTheStaffContactOfADepartmentAdminRevokesItsScope() {
        World w = world();
        TenantContext adminA = fixture.departmentAdmin(w.owner(), "admin-a", w.deptA());
        ContactView adminContact = contacts.create(w.owner(), w.listId(), ContactKind.STAFF,
                ContactDraft.named("管理员甲").withEmployeeId("ADM-1").withUnit(w.deptA()).withActor("admin-a"));

        contacts.disable(w.owner(), adminContact.id());

        assertThat(scopes.list(w.owner())).extracting(ContactScopeView::actorId).doesNotContain("admin-a");
        assertThat(contacts.search(adminA, ContactQuery.all()).items()).isEmpty();
    }

    @Test
    void aPlatformAccountCanOnlyBeAttachedToAStaffContact() {
        World w = world();
        UUID audience = fixture.list(w.owner(), "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();

        assertThatThrownBy(() -> contacts.create(w.owner(), audience, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com").withActor("admin-a")))
                .isInstanceOf(InvalidContactRequestException.class);
    }

    @Test
    void aScopeGrantMustPointAtAKnownUnit() {
        World w = world();

        assertThatThrownBy(() -> scopes.grant(w.owner(), "admin-a", UUID.randomUUID(), null))
                .isInstanceOf(ContactNotFoundException.class);
    }
}
