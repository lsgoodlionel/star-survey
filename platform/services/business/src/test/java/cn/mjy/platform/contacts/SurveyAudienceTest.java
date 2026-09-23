package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 问卷受众（WP-18 / WP-05 接缝）：一份问卷选定哪一批联系人，发布时据此物化成参与者。
 * 选择本身按条件保存（名单 / 部门 / 标签），每次发布现算——名单变了，下一版就跟着变。
 */
@SpringBootTest
class SurveyAudienceTest {

    @Autowired
    private SurveyFixture surveys;

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private SurveyAudienceService audiences;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace ws;
    private UUID survey;
    private UUID list;

    @BeforeEach
    void aDraftSurveyAndAContactList() {
        ws = surveys.workspace();
        survey = surveys.newSurvey(ws).id();
        list = fixture.list(ws.owner(), "受众 " + UUID.randomUUID(), ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
    }

    private ContactView contact(String name, String email) {
        return contacts.create(ws.owner(), list, ContactKind.RESPONDENT,
                ContactDraft.named(name).withEmail(email));
    }

    private ContactView contactIn(TenantContext ctx, UUID unitId, String name, String email) {
        ContactView created = contacts.create(ctx, list, ContactKind.RESPONDENT,
                ContactDraft.named(name).withEmail(email).withUnit(unitId));
        return created;
    }

    @Test
    void anAudienceSelectsTheContactsOfOneList() {
        ContactView ann = contact("甲", "ann@example.com");
        ContactView bob = contact("乙", "bob@example.com");

        audiences.set(ws.owner(), survey, list, null, true, null);

        assertThat(audiences.find(ws.owner(), survey)).get()
                .extracting(SurveyAudienceView::listId).isEqualTo(list);
        assertThat(audiences.recipients(ws.owner(), survey)).extracting(ContactView::id)
                .containsExactlyInAnyOrder(ann.id(), bob.id());
    }

    @Test
    void anAudienceNeedsAtLeastOneCriterion() {
        assertThatThrownBy(() -> audiences.set(ws.owner(), survey, null, null, true, null))
                .isInstanceOf(InvalidContactRequestException.class);
    }

    @Test
    void replacingTheAudienceKeepsOnlyTheLatestSelection() {
        UUID other = fixture.list(ws.owner(), "另一批 " + UUID.randomUUID(),
                ContactKind.RESPONDENT, DedupeKey.EMAIL).id();

        audiences.set(ws.owner(), survey, list, null, true, null);
        audiences.set(ws.owner(), survey, other, null, true, null);

        assertThat(audiences.find(ws.owner(), survey)).get()
                .extracting(SurveyAudienceView::listId).isEqualTo(other);
    }

    @Test
    void clearingTheAudienceLeavesNothingToInvite() {
        contact("甲", "ann@example.com");
        audiences.set(ws.owner(), survey, list, null, true, null);

        audiences.clear(ws.owner(), survey);

        assertThat(audiences.find(ws.owner(), survey)).isEmpty();
        assertThat(audiences.recipients(ws.owner(), survey)).isEmpty();
    }

    @Test
    void aDisabledContactIsNotInvited() {
        ContactView ann = contact("甲", "ann@example.com");
        ContactView bob = contact("乙", "bob@example.com");
        contacts.disable(ws.owner(), bob.id());
        audiences.set(ws.owner(), survey, list, null, true, null);

        assertThat(audiences.recipients(ws.owner(), survey)).extracting(ContactView::id)
                .containsExactly(ann.id());
    }

    @Test
    void anAudienceNarrowsToADepartmentSubtree() {
        OrgUnitView head = fixture.unit(ws.owner(), null, "总部");
        OrgUnitView team = fixture.unit(ws.owner(), head.id(), "一组");
        ContactView inTeam = contactIn(ws.owner(), team.id(), "甲", "ann@example.com");
        contact("乙", "bob@example.com");

        audiences.set(ws.owner(), survey, list, head.id(), true, null);

        assertThat(audiences.recipients(ws.owner(), survey)).extracting(ContactView::id)
                .containsExactly(inTeam.id());
    }

    @Test
    void theRecipientsAreCutToWhatTheCallerIsAllowedToSee() {
        OrgUnitView mine = fixture.unit(ws.owner(), null, "我的部门");
        OrgUnitView theirs = fixture.unit(ws.owner(), null, "别人的部门");
        ContactView visible = contactIn(ws.owner(), mine.id(), "甲", "ann@example.com");
        contactIn(ws.owner(), theirs.id(), "乙", "bob@example.com");
        audiences.set(ws.owner(), survey, list, null, true, null);
        TenantContext departmentAdmin = fixture.departmentAdmin(ws.owner(), "audience-admin", mine.id());

        List<ContactView> forAdmin = audiences.recipients(departmentAdmin, survey);

        assertThat(forAdmin).extracting(ContactView::id).containsExactly(visible.id());
        assertThat(audiences.recipients(ws.owner(), survey)).hasSize(2);
    }

    @Test
    void anotherTenantNeitherSeesNorSetsTheAudience() {
        audiences.set(ws.owner(), survey, list, null, true, null);
        TenantContext stranger = fixture.newTenant();

        assertThatThrownBy(() -> audiences.find(stranger, survey))
                .isInstanceOf(ContactNotFoundException.class);
        assertThatThrownBy(() -> audiences.recipients(stranger, survey))
                .isInstanceOf(ContactNotFoundException.class);
        assertThatThrownBy(() -> audiences.set(stranger, survey, list, null, true, null))
                .isInstanceOf(ContactNotFoundException.class);
    }

    /**
     * 读受众也要问"这是一份我看得见的问卷吗"：不然就能按 surveyId 枚举出"谁被邀请了"。
     *
     * <p>这条同时是<b>防重构腐烂</b>的钉子：把 {@code find}／{@code recipients} 里的
     * {@code requireSurvey} 去掉，两个方法会退回"查不到就当空"，本用例立刻转红。
     * 真正的"有通讯录权限、却对该问卷无 VIEW"那一支现行角色目录构造不出来，见 ADR 0017 诚实清单。
     */
    @Test
    void readingTheAudienceOfSomethingThatIsNotAVisibleSurveyIsRefused() {
        UUID notASurvey = ws.project();
        UUID nowhere = UUID.randomUUID();
        UUID anotherTenantsSurvey = surveys.newSurvey(surveys.workspace()).id();

        for (UUID unreachable : List.of(notASurvey, nowhere, anotherTenantsSurvey)) {
            assertThatThrownBy(() -> audiences.find(ws.owner(), unreachable))
                    .as("find %s", unreachable).isInstanceOf(ContactNotFoundException.class);
            assertThatThrownBy(() -> audiences.recipients(ws.owner(), unreachable))
                    .as("recipients %s", unreachable).isInstanceOf(ContactNotFoundException.class);
        }
    }

    /** 新表的跨租户负面测试（CONVENTIONS.md 硬性要求）：由数据库拒绝，不靠服务层判断。 */
    @Test
    void anotherTenantsRawSqlNeitherReadsNorWritesTheAudienceRow() {
        audiences.set(ws.owner(), survey, list, null, true, null);
        TenantId mine = ws.tenant();
        TenantId theirs = fixture.newTenant().tenantId();

        assertThat(count(mine)).isEqualTo(1);
        assertThat(count(theirs)).isZero();
        assertThat(tenantScope.call(theirs, () -> jdbc
                .sql("UPDATE survey_audience SET updated_by = 'hijacked'").update())).isZero();
        assertThatThrownBy(() -> tenantScope.run(theirs, () -> jdbc.sql("""
                INSERT INTO survey_audience (tenant_id, survey_id, list_id, updated_by)
                VALUES (:mine, :survey, :list, 'attacker')
                """).param("mine", mine.value()).param("survey", survey).param("list", list).update()))
                .rootCause().hasMessageContaining("row-level security");
        assertThat(jdbc.sql("SELECT count(*) FROM survey_audience").query(Long.class).single())
                .as("没有租户作用域时什么也看不到").isZero();
    }

    private long count(TenantId viewer) {
        return tenantScope.call(viewer,
                () -> jdbc.sql("SELECT count(*) FROM survey_audience").query(Long.class).single());
    }

    @Test
    void anAudienceCannotPointAtAListTheCallerCannotSee() {
        TenantContext stranger = fixture.newTenant();
        UUID strangerList = fixture.list(stranger, "别家名单", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();

        assertThatThrownBy(() -> audiences.set(ws.owner(), survey, strangerList, null, true, null))
                .isInstanceOf(ContactNotFoundException.class);
    }
}
