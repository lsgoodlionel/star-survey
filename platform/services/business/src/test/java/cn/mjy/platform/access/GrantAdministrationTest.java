package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
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
import org.springframework.security.access.AccessDeniedException;

/** 授权变更：由服务端判定能否授出、不能越权授出，每一次变更都进审计。 */
@SpringBootTest
class GrantAdministrationTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private GrantService grants;

    @Autowired
    private MemberService members;

    @Autowired
    private AccessDecisionService access;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantContext owner;
    private UUID project;
    private UUID survey;

    @BeforeEach
    void tenant() {
        owner = fixture.newTenant();
        project = fixture.project(owner.tenantId());
        survey = fixture.survey(owner.tenantId(), project);
    }

    @Test
    void grantingAndRevokingAreBothAudited() {
        fixture.activeMember(owner, "editor");

        UUID grantId = grants.grant(owner, new GrantRequest("editor", "editor", survey, null));
        grants.revoke(owner, grantId);

        List<String> actions = auditActions();
        assertThat(actions).contains("access.grant.create", "access.grant.revoke");
        assertThat(auditResources()).anyMatch(r -> r.contains(grantId.toString()) && r.contains("role=editor"));
    }

    @Test
    void aMemberWithoutManageMembersCannotGrant() {
        TenantContext editor = fixture.member(owner, "editor", "editor", survey);
        fixture.activeMember(owner, "other");
        int auditedBefore = auditActions().size();

        assertThatThrownBy(() -> grants.grant(editor, new GrantRequest("other", "editor", survey, null)))
                .isInstanceOf(AccessDeniedException.class);
        assertThat(auditActions()).hasSize(auditedBefore);
        assertThat(grants.list(owner)).noneMatch(g -> g.actorId().equals("other"));
    }

    @Test
    void aProjectManagerMayGrantWithinTheirProjectButNotOutsideIt() {
        TenantContext manager = fixture.member(owner, "pm", "project_manager", project);
        UUID otherProject = fixture.project(owner.tenantId());
        fixture.activeMember(owner, "editor");

        grants.grant(manager, new GrantRequest("editor", "editor", survey, null));

        assertThat(access.can(AccessFixture.context(owner.tenantId(), "editor"), Permission.EDIT, survey).allowed())
                .isTrue();
        assertThatThrownBy(() -> grants.grant(manager, new GrantRequest("editor", "editor", otherProject, null)))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void nobodyCanGrantPermissionsTheyDoNotHoldThemselves() {
        TenantContext manager = fixture.member(owner, "pm", "project_manager", project);
        TenantContext admin = fixture.member(owner, "admin", "org_admin", null);
        fixture.activeMember(owner, "target");

        // 项目管理者没有"查看敏感字段"，不能授出含该权限的原始数据查看者。
        assertThatThrownBy(() -> grants.grant(manager, new GrantRequest("target", "raw_data_viewer", survey, null)))
                .isInstanceOf(AccessDeniedException.class);
        // 组织管理员没有财务权限，也不能把别人提升为租户所有者。
        assertThatThrownBy(() -> grants.grant(admin, new GrantRequest("target", "finance", null, null)))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> grants.grant(admin, new GrantRequest("target", "tenant_owner", null, null)))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void aTenantWideOnlyRoleCannotBeScopedToAResource() {
        fixture.activeMember(owner, "fin");

        assertThatThrownBy(() -> grants.grant(owner, new GrantRequest("fin", "finance", project, null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void supportAccessMustCarryAnExpiryWithinTheRoleLimit() {
        fixture.activeMember(owner, "support");

        assertThatThrownBy(() -> grants.grant(owner, new GrantRequest("support", "support", survey, null)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> grants.grant(owner, new GrantRequest("support", "support", survey,
                Instant.now().plus(30, ChronoUnit.DAYS)))).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> grants.grant(owner, new GrantRequest("support", "support", survey,
                Instant.now().minus(1, ChronoUnit.HOURS)))).isInstanceOf(IllegalArgumentException.class);

        grants.grant(owner, new GrantRequest("support", "support", survey, Instant.now().plus(2, ChronoUnit.HOURS)));
    }

    @Test
    void anUnknownRoleOrNonMemberIsRejected() {
        fixture.activeMember(owner, "member");

        assertThatThrownBy(() -> grants.grant(owner, new GrantRequest("member", "superuser", null, null)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> grants.grant(owner, new GrantRequest("nobody", "editor", survey, null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void theLastTenantOwnerCannotBeRevokedOrRemoved() {
        UUID ownerGrant = grants.list(owner).stream()
                .filter(g -> g.roleCode().equals("tenant_owner")).findFirst().orElseThrow().id();

        assertThatThrownBy(() -> grants.revoke(owner, ownerGrant)).isInstanceOf(IllegalStateException.class);
        assertThatThrownBy(() -> members.remove(owner, AccessFixture.OWNER)).isInstanceOf(IllegalStateException.class);
    }

    @Test
    void anAdministratorCannotRevokeTheOwner() {
        TenantContext admin = fixture.member(owner, "admin", "org_admin", null);
        fixture.member(owner, "second-owner", "tenant_owner", null);
        UUID ownerGrant = grants.list(owner).stream()
                .filter(g -> g.actorId().equals(AccessFixture.OWNER)).findFirst().orElseThrow().id();

        assertThatThrownBy(() -> grants.revoke(admin, ownerGrant)).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void removingAMemberRevokesTheirGrantsAndIsAudited() {
        TenantContext editor = fixture.member(owner, "editor", "editor", survey);

        members.remove(owner, "editor");

        assertThat(access.can(editor, Permission.VIEW, survey).allowed()).isFalse();
        assertThat(grants.list(owner)).noneMatch(g -> g.actorId().equals("editor"));
        assertThat(auditActions()).contains("access.member.remove", "access.grant.revoke");
    }

    @Test
    void grantingTheSameRoleTwiceIsIdempotent() {
        fixture.activeMember(owner, "editor");

        UUID first = grants.grant(owner, new GrantRequest("editor", "editor", survey, null));
        UUID second = grants.grant(owner, new GrantRequest("editor", "editor", survey, null));

        assertThat(second).isEqualTo(first);
        assertThat(grants.list(owner)).filteredOn(g -> g.actorId().equals("editor")).hasSize(1);
    }

    private List<String> auditActions() {
        return tenantScope.call(owner.tenantId(),
                () -> jdbc.sql("SELECT action FROM audit_log ORDER BY id").query(String.class).list());
    }

    private List<String> auditResources() {
        return tenantScope.call(owner.tenantId(),
                () -> jdbc.sql("SELECT resource FROM audit_log ORDER BY id").query(String.class).list());
    }
}
