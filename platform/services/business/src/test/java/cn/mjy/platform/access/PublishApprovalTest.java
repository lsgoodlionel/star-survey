package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;

/** WP-01 冻结决定：子账户发布需审核、管理员免审，审核开关按租户可配。 */
@SpringBootTest
class PublishApprovalTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private AccessDecisionService access;

    @Autowired
    private AccessSettingsService settings;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private AuditLogRepository auditLog;

    private TenantContext owner;
    private UUID survey;
    private TenantContext editor;

    @BeforeEach
    void tenantWithAnEditor() {
        owner = fixture.newTenant();
        survey = fixture.survey(owner.tenantId(), fixture.project(owner.tenantId()));
        editor = fixture.member(owner, "sub-account", "editor", survey);
    }

    @Test
    void approvalIsRequiredByDefault() {
        assertThat(settings.isPublishApprovalRequired(owner.tenantId())).isTrue();
    }

    @Test
    void aSubAccountCannotPublishWithoutApprovalWhenTheTenantRequiresIt() {
        settings.setPublishApprovalRequired(owner, true);

        Decision decision = access.can(editor, Permission.PUBLISH, survey);

        assertThat(decision.allowed()).isFalse();
        assertThat(decision.reason()).isEqualTo(DecisionReason.APPROVAL_REQUIRED);
    }

    @Test
    void aSubAccountMayPublishWhenTheApprovalSwitchIsOff() {
        settings.setPublishApprovalRequired(owner, false);

        Decision decision = access.can(editor, Permission.PUBLISH, survey);

        assertThat(decision.allowed()).isTrue();
    }

    @Test
    void administratorsPublishWithoutApprovalEvenWhenRequired() {
        TenantContext admin = fixture.member(owner, "admin", "org_admin", null);
        settings.setPublishApprovalRequired(owner, true);

        assertThat(access.can(owner, Permission.PUBLISH, survey).allowed()).isTrue();
        assertThat(access.can(admin, Permission.PUBLISH, survey).allowed()).isTrue();
    }

    @Test
    void theApprovalSwitchDoesNotLetSomeoneWithoutPublishRightsPublish() {
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", survey);
        settings.setPublishApprovalRequired(owner, false);

        Decision decision = access.can(viewer, Permission.PUBLISH, survey);

        assertThat(decision.allowed()).isFalse();
        assertThat(decision.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT);
    }

    @Test
    void aPublishReviewerMayApproveButNotPublish() {
        TenantContext reviewer = fixture.member(owner, "reviewer", "publish_reviewer", survey);

        assertThat(access.can(reviewer, Permission.APPROVE_PUBLISH, survey).allowed()).isTrue();
        assertThat(access.can(reviewer, Permission.PUBLISH, survey).allowed()).isFalse();
        assertThat(access.can(editor, Permission.APPROVE_PUBLISH, survey).allowed()).isFalse();
    }

    @Test
    void onlyThoseWhoManageSettingsMayChangeTheSwitch() {
        assertThatThrownBy(() -> settings.setPublishApprovalRequired(editor, false))
                .isInstanceOf(AccessDeniedException.class);
        assertThat(settings.isPublishApprovalRequired(owner.tenantId())).isTrue();
    }

    @Test
    void changingTheSwitchIsAudited() {
        long before = tenantScope.call(owner.tenantId(), auditLog::countAll);

        settings.setPublishApprovalRequired(owner, false);

        assertThat(tenantScope.call(owner.tenantId(), auditLog::countAll)).isEqualTo(before + 1);
    }
}
