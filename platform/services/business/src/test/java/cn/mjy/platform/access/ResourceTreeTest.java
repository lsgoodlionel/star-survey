package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionTemplate;

/** 资源树管理（ADR 0011 规则 1、2）：建项目 / 文件夹、改名、移动，以及移动后授权按新位置重算。 */
@SpringBootTest
class ResourceTreeTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private ResourceTreeService tree;

    @Autowired
    private AccessDecisionService access;

    @Autowired
    private GrantService grants;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private TransactionTemplate transactions;

    private TenantContext owner;
    private TenantContext admin;

    @BeforeEach
    void setUp() {
        owner = fixture.newTenant();
        admin = fixture.member(owner, "admin", "org_admin", null);
    }

    // ------------------------------------------------------------ 建项目

    @Test
    void anAdminCreatesAProjectAndBecomesItsProjectManager() {
        ResourceView project = tree.createProject(admin, "  市场调研  ");

        assertThat(project.kind()).isEqualTo("project");
        assertThat(project.parentId()).isNull();
        assertThat(project.name()).isEqualTo("市场调研");
        assertThat(grants.list(owner)).anySatisfy(g -> {
            assertThat(g.actorId()).isEqualTo("admin");
            assertThat(g.roleCode()).isEqualTo(ResourceTreeService.CREATOR_ROLE);
            assertThat(g.resourceId()).isEqualTo(project.id());
        });
        assertThat(auditCount(owner, "access.resource.create", project.id())).isEqualTo(1);
        assertThat(auditCount(owner, "access.grant.create", project.id())).isEqualTo(1);
    }

    @Test
    void theCreatorKeepsManagingTheProjectAfterLosingTenantWideAdmin() {
        ResourceView project = tree.createProject(admin, "项目");
        UUID adminGrant = grants.list(owner).stream()
                .filter(g -> g.actorId().equals("admin") && g.resourceId() == null).findFirst().orElseThrow().id();

        grants.revoke(owner, adminGrant);

        assertThat(access.can(admin, Permission.EDIT, project.id()).allowed()).isTrue();
        assertThat(access.can(admin, Permission.MANAGE_MEMBERS, project.id()).allowed()).isTrue();
    }

    @Test
    void aMemberWithoutTenantLevelManageSettingsCannotCreateAProject() {
        UUID existing = tree.createProject(admin, "已有项目").id();
        TenantContext manager = fixture.member(owner, "pm", "project_manager", existing);

        assertThatThrownBy(() -> tree.createProject(manager, "新项目"))
                .isInstanceOf(ResourceAccessDeniedException.class)
                .satisfies(e -> assertThat(((ResourceAccessDeniedException) e).reason())
                        .isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThat(visibleNames(owner)).containsExactly("已有项目");
    }

    @Test
    void aBlankOrOverlongNameIsRejected() {
        assertThatThrownBy(() -> tree.createProject(admin, "   ")).isInstanceOf(InvalidResourceRequestException.class);
        assertThatThrownBy(() -> tree.createProject(admin, "x".repeat(ResourceTreeService.MAX_NAME_LENGTH + 1)))
                .isInstanceOf(InvalidResourceRequestException.class);
    }

    // ------------------------------------------------------------ 建文件夹

    @Test
    void anEditorOnTheProjectCreatesNestedFolders() {
        UUID project = tree.createProject(admin, "项目").id();
        TenantContext editor = fixture.member(owner, "ed", "editor", project);

        ResourceView folder = tree.createFolder(editor, project, "一季度");
        ResourceView nested = tree.createFolder(editor, folder.id(), "三月");

        assertThat(folder.kind()).isEqualTo("folder");
        assertThat(folder.parentId()).isEqualTo(project);
        assertThat(nested.parentId()).isEqualTo(folder.id());
        assertThat(auditCount(owner, "access.resource.create", nested.id())).isEqualTo(1);
    }

    @Test
    void aViewerCannotCreateAFolder() {
        UUID project = tree.createProject(admin, "项目").id();
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", project);

        assertThatThrownBy(() -> tree.createFolder(viewer, project, "不该出现"))
                .isInstanceOf(ResourceAccessDeniedException.class);
        assertThat(visibleNames(owner)).containsExactly("项目");
    }

    @Test
    void aFolderCannotBeCreatedUnderASurveyOrAnUnknownParent() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID survey = fixture.survey(owner.tenantId(), project);

        assertThatThrownBy(() -> tree.createFolder(admin, survey, "叶子下面"))
                .isInstanceOf(InvalidResourceRequestException.class);
        assertThatThrownBy(() -> tree.createFolder(admin, UUID.randomUUID(), "无父"))
                .isInstanceOf(ResourceNotFoundException.class);
    }

    // ------------------------------------------------------------ 查询与改名

    @Test
    void listingShowsOnlyNodesTheCallerCanViewIncludingInheritedOnes() {
        UUID p1 = tree.createProject(admin, "P1").id();
        UUID p2 = tree.createProject(admin, "P2").id();
        UUID f1 = tree.createFolder(admin, p1, "F1").id();
        fixture.survey(owner.tenantId(), f1);
        tree.createFolder(admin, p2, "F2");
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", f1);

        assertThat(visibleNames(viewer)).containsExactlyInAnyOrder("F1", "问卷");
        assertThat(visibleNames(admin)).containsExactlyInAnyOrder("P1", "P2", "F1", "F2", "问卷");
        assertThat(tree.list(viewer, f1, null, 10).items()).extracting(ResourceView::name).containsExactly("问卷");
    }

    @Test
    void listingPagesThroughWithACursorWithoutDuplicatesOrGaps() {
        for (int i = 0; i < 5; i++) {
            tree.createProject(admin, "P" + i);
        }

        ResourcePage first = tree.list(admin, null, null, 2);
        ResourcePage second = tree.list(admin, null, first.nextCursor(), 2);
        ResourcePage third = tree.list(admin, null, second.nextCursor(), 2);

        assertThat(first.items()).hasSize(2);
        assertThat(second.items()).hasSize(2);
        assertThat(third.items()).hasSize(1);
        assertThat(third.nextCursor()).isNull();
        assertThat(java.util.stream.Stream.of(first, second, third).flatMap(p -> p.items().stream())
                .map(ResourceView::name).distinct()).hasSize(5);
    }

    @Test
    void getRequiresViewAndUnknownIdsAreNotFound() {
        UUID project = tree.createProject(admin, "项目").id();
        TenantContext outsider = fixture.activeMember(owner, "outsider");

        assertThat(tree.get(admin, project).name()).isEqualTo("项目");
        assertThatThrownBy(() -> tree.get(outsider, project)).isInstanceOf(ResourceAccessDeniedException.class);
        assertThatThrownBy(() -> tree.get(admin, UUID.randomUUID())).isInstanceOf(ResourceNotFoundException.class);
    }

    @Test
    void renameNeedsEditOnTheNodeAndIsAudited() {
        UUID project = tree.createProject(admin, "旧名").id();
        UUID folder = tree.createFolder(admin, project, "文件夹").id();
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", project);

        assertThatThrownBy(() -> tree.rename(viewer, folder, "偷改")).isInstanceOf(ResourceAccessDeniedException.class);
        assertThat(tree.rename(admin, project, "新名").name()).isEqualTo("新名");
        assertThat(auditCount(owner, "access.resource.rename", project)).isEqualTo(1);
    }

    @Test
    void aSurveyCannotBeRenamedThroughTheTreeBecauseItsTitleComesFromItsDefinition() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID survey = fixture.survey(owner.tenantId(), project);

        assertThatThrownBy(() -> tree.rename(admin, survey, "改名")).isInstanceOf(InvalidResourceRequestException.class);
    }

    // ------------------------------------------------------------ 移动

    @Test
    void movingAFolderNeedsEditOnBothTheSourceAndTheTarget() {
        UUID p1 = tree.createProject(admin, "P1").id();
        UUID p2 = tree.createProject(admin, "P2").id();
        UUID folder = tree.createFolder(admin, p1, "F").id();
        TenantContext editorOnSourceOnly = fixture.member(owner, "src-editor", "editor", p1);
        TenantContext viewerOfTarget = fixture.member(owner, "tgt-viewer", "statistics_viewer", p2);
        grants.grant(owner, new GrantRequest("src-editor", "statistics_viewer", p2, null));

        assertThatThrownBy(() -> tree.move(editorOnSourceOnly, folder, p2))
                .isInstanceOf(ResourceAccessDeniedException.class);
        assertThatThrownBy(() -> tree.move(viewerOfTarget, folder, p2))
                .isInstanceOf(ResourceAccessDeniedException.class);
        assertThat(tree.get(admin, folder).parentId()).isEqualTo(p1);
    }

    @Test
    void aViewerCannotMoveANode() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID a = tree.createFolder(admin, project, "A").id();
        UUID b = tree.createFolder(admin, project, "B").id();
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", project);

        assertThatThrownBy(() -> tree.move(viewer, a, b)).isInstanceOf(ResourceAccessDeniedException.class);
        assertThat(auditCount(owner, "access.resource.move", a)).isZero();
    }

    @Test
    void anEditorOnBothPlacesMovesAFolderAcrossProjectsAndItIsAudited() {
        UUID p1 = tree.createProject(admin, "P1").id();
        UUID p2 = tree.createProject(admin, "P2").id();
        UUID folder = tree.createFolder(admin, p1, "F").id();
        TenantContext editor = fixture.member(owner, "ed", "editor", p1);
        grants.grant(owner, new GrantRequest("ed", "editor", p2, null));

        ResourceView moved = tree.move(editor, folder, p2);

        assertThat(moved.parentId()).isEqualTo(p2);
        assertThat(auditCount(owner, "access.resource.move", folder)).isEqualTo(1);
    }

    @Test
    void aNodeCannotBeMovedUnderItselfOrItsOwnDescendant() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID parent = tree.createFolder(admin, project, "父").id();
        UUID child = tree.createFolder(admin, parent, "子").id();
        UUID grandchild = tree.createFolder(admin, child, "孙").id();

        assertThatThrownBy(() -> tree.move(admin, parent, grandchild)).isInstanceOf(ResourceConflictException.class);
        assertThatThrownBy(() -> tree.move(admin, parent, parent)).isInstanceOf(ResourceConflictException.class);
        assertThat(tree.get(admin, parent).parentId()).isEqualTo(project);
    }

    @Test
    void projectsCannotBeMovedAndNothingMovesUnderASurvey() {
        UUID p1 = tree.createProject(admin, "P1").id();
        UUID p2 = tree.createProject(admin, "P2").id();
        UUID folder = tree.createFolder(admin, p1, "F").id();
        UUID survey = fixture.survey(owner.tenantId(), p1);

        assertThatThrownBy(() -> tree.move(admin, p1, p2)).isInstanceOf(InvalidResourceRequestException.class);
        assertThatThrownBy(() -> tree.move(admin, folder, survey)).isInstanceOf(InvalidResourceRequestException.class);
        assertThatThrownBy(() -> tree.move(admin, folder, UUID.randomUUID())).isInstanceOf(ResourceNotFoundException.class);
    }

    @Test
    void aSurveyIsMovedWithTheSameServiceLikeAnyLeaf() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID folder = tree.createFolder(admin, project, "F").id();
        UUID survey = fixture.survey(owner.tenantId(), project);

        assertThat(tree.move(admin, survey, folder).parentId()).isEqualTo(folder);
        assertThat(tree.list(admin, folder, null, 10).items()).extracting(ResourceView::id).containsExactly(survey);
    }

    @Test
    void afterAMoveInheritedGrantsFollowTheNewPositionAndDirectGrantsStay() {
        UUID p1 = tree.createProject(admin, "P1").id();
        UUID p2 = tree.createProject(admin, "P2").id();
        UUID folder = tree.createFolder(admin, p1, "F").id();
        UUID survey = fixture.survey(owner.tenantId(), folder);
        TenantContext oldSide = fixture.member(owner, "old-side", "statistics_viewer", p1);
        TenantContext newSide = fixture.member(owner, "new-side", "editor", p2);
        TenantContext direct = fixture.member(owner, "direct", "raw_data_viewer", folder);
        assertThat(access.can(oldSide, Permission.VIEW, survey).allowed()).isTrue();
        assertThat(access.can(newSide, Permission.EDIT, survey).allowed()).isFalse();

        tree.move(admin, folder, p2);

        assertThat(access.can(oldSide, Permission.VIEW, survey).allowed()).isFalse();
        assertThat(access.can(oldSide, Permission.VIEW, folder).allowed()).isFalse();
        assertThat(access.can(newSide, Permission.EDIT, survey).allowed()).isTrue();
        assertThat(access.can(direct, Permission.VIEW_RAW_RESPONSES, survey).allowed()).isTrue();
        assertThat(grants.list(owner)).anySatisfy(g -> {
            assertThat(g.actorId()).isEqualTo("direct");
            assertThat(g.resourceId()).isEqualTo(folder);
        });
    }

    @Test
    void movingToTheCurrentParentIsANoOpThatIsNotAudited() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID folder = tree.createFolder(admin, project, "F").id();

        assertThat(tree.move(admin, folder, project).parentId()).isEqualTo(project);
        assertThat(auditCount(owner, "access.resource.move", folder)).isZero();
    }

    // ------------------------------------------------------------ helpers

    private java.util.List<String> visibleNames(TenantContext ctx) {
        return tree.list(ctx, null, null, ResourceTreeService.MAX_PAGE_SIZE).items().stream()
                .map(ResourceView::name).toList();
    }

    private long auditCount(TenantContext ctx, String action, UUID resource) {
        return transactions.execute(status -> {
            jdbc.sql("SELECT set_config('app.tenant_id', :t, true)").param("t", ctx.tenantId().value().toString())
                    .query(String.class).single();
            return jdbc.sql("SELECT COUNT(*) FROM audit_log WHERE action = :action AND resource LIKE :pattern")
                    .param("action", action)
                    .param("pattern", "%" + resource + "%")
                    .query(Long.class)
                    .single();
        });
    }
}
