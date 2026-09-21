package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 判定服务：角色权限 + 资源范围向下继承，结果带原因。 */
@SpringBootTest
class AccessDecisionTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private AccessDecisionService access;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantContext owner;
    private TenantId tenant;
    private UUID project;
    private UUID folder;
    private UUID siblingFolder;
    private UUID survey;
    private UUID siblingSurvey;

    @BeforeEach
    void resourceTree() {
        owner = fixture.newTenant();
        tenant = owner.tenantId();
        project = fixture.project(tenant);
        folder = fixture.folder(tenant, project);
        siblingFolder = fixture.folder(tenant, project);
        survey = fixture.survey(tenant, folder);
        siblingSurvey = fixture.survey(tenant, siblingFolder);
    }

    @Test
    void theStatisticsViewerMayNotViewOrExportRawResponses() {
        TenantContext viewer = fixture.member(owner, "stats", "statistics_viewer", survey);

        Decision view = access.can(viewer, Permission.VIEW_RAW_RESPONSES, survey);
        Decision export = access.can(viewer, Permission.EXPORT_RAW_RESPONSES, survey);

        assertThat(view.allowed()).isFalse();
        assertThat(view.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT);
        assertThat(export.allowed()).isFalse();
        assertThat(export.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT);
        assertThat(access.can(viewer, Permission.VIEW_STATISTICS, survey).allowed()).isTrue();
    }

    @Test
    void aFolderGrantIsInheritedByASurveyInsideIt() {
        TenantContext editor = fixture.member(owner, "editor", "editor", folder);

        Decision decision = access.can(editor, Permission.EDIT, survey);

        assertThat(decision.allowed()).isTrue();
        assertThat(decision.reason()).isEqualTo(DecisionReason.GRANTED);
        assertThat(decision.detail()).contains("editor").contains(folder.toString());
    }

    @Test
    void aFolderGrantDoesNotReachSiblingFoldersOrTheirSurveys() {
        TenantContext editor = fixture.member(owner, "editor", "editor", folder);

        assertThat(access.can(editor, Permission.EDIT, siblingFolder).allowed()).isFalse();
        assertThat(access.can(editor, Permission.EDIT, siblingSurvey).allowed()).isFalse();
        assertThat(access.can(editor, Permission.EDIT, project).allowed()).isFalse();
    }

    @Test
    void aProjectGrantReachesNestedFoldersAndSurveys() {
        UUID nested = fixture.folder(tenant, folder);
        UUID deepSurvey = fixture.survey(tenant, nested);
        TenantContext manager = fixture.member(owner, "pm", "project_manager", project);

        assertThat(access.can(manager, Permission.VIEW_RAW_RESPONSES, deepSurvey).allowed()).isTrue();
        assertThat(access.can(manager, Permission.EDIT, siblingSurvey).allowed()).isTrue();
    }

    @Test
    void theClosestGrantIsReportedAsTheReasonWhenSeveralApply() {
        TenantContext member = fixture.member(owner, "multi", "statistics_viewer", project);
        grantAlso(member, "editor", folder);

        Decision decision = access.can(member, Permission.VIEW, survey);

        assertThat(decision.allowed()).isTrue();
        assertThat(decision.detail()).contains("editor").contains(folder.toString());
    }

    @Test
    void aTenantWideGrantCoversEveryResource() {
        TenantContext admin = fixture.member(owner, "admin", "org_admin", null);

        assertThat(access.can(admin, Permission.EDIT, siblingSurvey).allowed()).isTrue();
        assertThat(access.can(admin, Permission.MANAGE_SETTINGS, null).allowed()).isTrue();
    }

    @Test
    void aResourceGrantDoesNotConferTenantLevelPermissions() {
        TenantContext manager = fixture.member(owner, "pm", "project_manager", project);

        Decision decision = access.can(manager, Permission.MANAGE_MEMBERS, null);

        assertThat(decision.allowed()).isFalse();
        assertThat(access.can(manager, Permission.MANAGE_MEMBERS, project).allowed()).isTrue();
    }

    @Test
    void anExpiredGrantNoLongerApplies() {
        TenantContext support = fixture.member(owner, "support", "support", survey,
                Instant.now().plus(1, ChronoUnit.HOURS));
        assertThat(access.can(support, Permission.VIEW, survey).allowed()).isTrue();

        // 把到期时间拨到过去（测试里直接改表，模拟时间流逝）。
        tenantScope.run(tenant, () -> jdbc.sql(
                "UPDATE access_grant SET expires_at = now() - interval '1 minute' WHERE actor_id = 'support'").update());

        assertThat(access.can(support, Permission.VIEW, survey).allowed()).isFalse();
    }

    @Test
    void anUnknownResourceIsDenied() {
        Decision decision = access.can(owner, Permission.VIEW, UUID.randomUUID());

        assertThat(decision.allowed()).isFalse();
        assertThat(decision.reason()).isEqualTo(DecisionReason.UNKNOWN_RESOURCE);
    }

    @Test
    void someoneWhoIsNotAMemberIsDenied() {
        Decision decision = access.can(AccessFixture.context(tenant, "stranger"), Permission.VIEW, survey);

        assertThat(decision.allowed()).isFalse();
        assertThat(decision.reason()).isEqualTo(DecisionReason.NOT_AN_ACTIVE_MEMBER);
    }

    @Test
    void anInvitedMemberHasNoAccessUntilTheyAccept() {
        TenantContext invitee = AccessFixture.context(tenant, "invitee");
        fixture.members().invite(owner, "invitee", MemberKind.STAFF);
        fixture.grants().grant(owner, new GrantRequest("invitee", "editor", survey, null));

        assertThat(access.can(invitee, Permission.VIEW, survey).reason())
                .isEqualTo(DecisionReason.NOT_AN_ACTIVE_MEMBER);

        fixture.members().acceptInvitation(invitee);
        assertThat(access.can(invitee, Permission.VIEW, survey).allowed()).isTrue();
    }

    @Test
    void theRolesClaimInTheTokenGrantsNothingByItself() {
        TenantContext claimsOwner = new TenantContext(tenant, "stranger", List.of("tenant_owner"), "t");

        assertThat(access.can(claimsOwner, Permission.VIEW, survey).allowed()).isFalse();
    }

    private void grantAlso(TenantContext member, String role, UUID resource) {
        fixture.grants().grant(owner, new GrantRequest(member.actorId(), role, resource, null));
    }
}
