package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.sql.Timestamp;
import java.text.Normalizer;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
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
    private AccessSettingsService settings;

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
    void defaultListUsesBusinessOrderAcrossPageBoundaries() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID folderZ = tree.createFolder(admin, project, "Z 文件夹").id();
        UUID folderA = tree.createFolder(admin, project, "A 文件夹").id();
        UUID surveyZ = fixture.survey(owner.tenantId(), project);
        UUID surveyA = fixture.survey(owner.tenantId(), project);
        Instant sameTime = Instant.parse("2026-10-09T08:00:00Z");
        setOrdering(project, "项目", sameTime);
        setOrdering(folderZ, "Z 文件夹", sameTime);
        setOrdering(folderA, "A 文件夹", sameTime);
        setOrdering(surveyZ, "Z 问卷", sameTime);
        setOrdering(surveyA, "A 问卷", sameTime);

        ResourcePage first = tree.list(admin, null, null, 2, null, null, null, null);
        ResourcePage second = tree.list(admin, null, first.nextCursor(), 2, null, null, null, null);
        ResourcePage third = tree.list(admin, null, second.nextCursor(), 2, null, null, null, null);

        List<UUID> ids = java.util.stream.Stream.of(first, second, third)
                .flatMap(page -> page.items().stream()).map(ResourceView::id).toList();
        assertThat(ids).containsExactly(project, folderA, folderZ, surveyA, surveyZ);
        assertThat(ids).doesNotHaveDuplicates();
        assertThat(third.nextCursor()).isNull();
    }

    @Test
    void updatedDescKeepsItsDirectionAcrossPageBoundaries() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID oldest = tree.createFolder(admin, project, "最早").id();
        UUID middle = tree.createFolder(admin, project, "中间").id();
        UUID newest = tree.createFolder(admin, project, "最新").id();
        setOrdering(oldest, "最早", Instant.parse("2026-10-09T08:00:00Z"));
        setOrdering(middle, "中间", Instant.parse("2026-10-09T09:00:00Z"));
        setOrdering(newest, "最新", Instant.parse("2026-10-09T10:00:00Z"));

        ResourcePage first = tree.list(admin, project, null, 2, null, "folder", null, "updated_desc");
        ResourcePage second = tree.list(admin, project, first.nextCursor(), 2,
                null, "folder", null, "updated_desc");

        assertThat(java.util.stream.Stream.of(first, second).flatMap(page -> page.items().stream())
                .map(ResourceView::id)).containsExactly(newest, middle, oldest);
        assertThat(second.nextCursor()).isNull();
    }

    @Test
    void updatedDescUsesIdAsTheFinalTieBreakerForSameTimeAndName() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID first = tree.createFolder(admin, project, "同名").id();
        UUID second = tree.createFolder(admin, project, "同名").id();
        Instant sameTime = Instant.parse("2026-10-09T08:00:00Z");
        setOrdering(first, "同名", sameTime);
        setOrdering(second, "同名", sameTime);
        List<UUID> expected = java.util.stream.Stream.of(first, second)
                .sorted(Comparator.comparing(UUID::toString)).toList();

        ResourcePage pageOne = tree.list(admin, project, null, 1, null, "folder", null, "updated_desc");
        ResourcePage pageTwo = tree.list(admin, project, pageOne.nextCursor(), 1,
                null, "folder", null, "updated_desc");

        assertThat(List.of(pageOne.items().getFirst().id(), pageTwo.items().getFirst().id()))
                .containsExactlyElementsOf(expected);
        assertThat(pageTwo.nextCursor()).isNull();
    }

    @Test
    void nameSortIsStableForNormalizedUnicodeNames() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID first = tree.createFolder(admin, project, "e\u0301").id();
        UUID second = tree.createFolder(admin, project, "\u00e9").id();
        List<UUID> expected = java.util.stream.Stream.of(first, second)
                .sorted(Comparator.comparing(UUID::toString)).toList();

        ResourcePage pageOne = tree.list(admin, project, null, 1, null, "folder", null, "name_asc");
        ResourcePage pageTwo = tree.list(admin, project, pageOne.nextCursor(), 1,
                null, "folder", null, "name_asc");

        assertThat(pageOne.items().getFirst().name()).isEqualTo("\u00e9");
        assertThat(pageTwo.items().getFirst().name()).isEqualTo("\u00e9");
        assertThat(List.of(pageOne.items().getFirst().id(), pageTwo.items().getFirst().id()))
                .containsExactlyElementsOf(expected);
    }

    @Test
    void filtersAfterAuthorizationWithoutLeakingHiddenMatches() {
        UUID visibleProject = tree.createProject(admin, "可见项目").id();
        UUID hiddenProject = tree.createProject(admin, "机密命中词").id();
        TenantContext viewer = fixture.member(owner, "filtered-viewer", "statistics_viewer", visibleProject);

        ResourcePage result = tree.list(viewer, null, null, 20,
                "机密命中词", "project", "active", "name_asc");

        assertThat(result.items()).isEmpty();
        assertThat(tree.get(admin, hiddenProject).name()).isEqualTo("机密命中词");
    }

    @Test
    void activeVisibilityExcludesArchivedAncestorsAndArchivedListsOnlyDirectlyArchivedNodes() {
        UUID project = tree.createProject(admin, "归档项目").id();
        UUID folder = tree.createFolder(admin, project, "未直接归档文件夹").id();
        UUID survey = fixture.survey(owner.tenantId(), folder);
        archive(project);

        assertThat(tree.list(admin, null, null, 20, null, null, null, null).items())
                .extracting(ResourceView::id).doesNotContain(project, folder, survey);
        assertThat(tree.list(admin, null, null, 20, null, null, "archived", null).items())
                .extracting(ResourceView::id).containsExactly(project);
    }

    @Test
    void businessChangesUpdateTimestampsAndReadsDoNot() {
        ResourceView project = tree.createProject(admin, "项目");
        ResourceView folder = tree.createFolder(admin, project.id(), "旧名");
        UUID target = tree.createFolder(admin, project.id(), "目标").id();
        assertThat(folder.updatedAt()).isEqualTo(folder.createdAt());
        assertThat(folder.archivedAt()).isNull();
        Instant old = Instant.parse("2020-01-01T00:00:00Z");
        setOrdering(folder.id(), folder.name(), old);

        ResourceView renamed = tree.rename(admin, folder.id(), "新名");
        assertThat(renamed.updatedAt()).isAfter(old);
        setOrdering(folder.id(), renamed.name(), old);
        ResourceView moved = tree.move(admin, folder.id(), target);
        assertThat(moved.updatedAt()).isAfter(old);

        Instant afterMove = moved.updatedAt();
        assertThat(tree.get(admin, folder.id()).updatedAt()).isEqualTo(afterMove);
        assertThat(tree.get(admin, folder.id()).updatedAt()).isEqualTo(afterMove);
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
    void capabilitiesUseInheritedDecisionsForEditorsAndViewers() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID folder = tree.createFolder(admin, project, "文件夹").id();
        UUID survey = fixture.survey(owner.tenantId(), folder);
        TenantContext editor = fixture.member(owner, "editor", "editor", project);
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", project);
        TenantContext reviewer = fixture.member(owner, "reviewer", "publish_reviewer", project);
        settings.setPublishApprovalRequired(owner, true);

        assertThat(tree.capabilities(editor, survey))
                .isEqualTo(new ResourceCapabilities(false, false, true, true, false, false, true, false));
        assertThat(tree.capabilities(viewer, survey))
                .isEqualTo(new ResourceCapabilities(false, false, false, false, false, false, false, false));
        assertThat(tree.capabilities(reviewer, survey))
                .isEqualTo(new ResourceCapabilities(false, false, false, false, false, true, false, false));
        assertThat(tree.capabilities(editor, folder).canCreateChildren()).isTrue();
        assertThat(tree.capabilities(admin, null).canCreateProject()).isTrue();
    }

    // ------------------------------------------------------------ 归档与恢复

    @Test
    void archiveHidesTheWholeSubtreeFromActiveLists() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID folder = tree.createFolder(admin, project, "文件夹").id();
        UUID survey = fixture.survey(owner.tenantId(), folder);

        ResourceView archived = tree.archive(admin, project);

        assertThat(archived.archivedAt()).isNotNull();
        assertThat(tree.get(admin, folder).archivedAt()).isNull();
        assertThat(tree.get(admin, survey).archivedAt()).isNull();
        assertThat(tree.list(admin, null, null, 20).items()).extracting(ResourceView::id)
                .doesNotContain(project, folder, survey);
        assertThat(tree.list(admin, project, null, 20).items()).extracting(ResourceView::id)
                .doesNotContain(folder, survey);
        assertThat(tree.list(admin, null, null, 20, null, null, "archived", null).items())
                .extracting(ResourceView::id).containsExactly(project);
        assertThat(tree.capabilities(admin, project).canArchive()).isFalse();
        assertThat(tree.capabilities(admin, project).canRestore()).isTrue();
        assertThat(tree.capabilities(admin, folder).canArchive()).isFalse();
        assertThat(tree.capabilities(admin, folder).canRestore()).isFalse();
    }

    @Test
    void restorePreservesAnIndependentlyArchivedDescendant() {
        UUID project = tree.createProject(admin, "项目").id();
        UUID folder = tree.createFolder(admin, project, "独立归档").id();
        UUID survey = fixture.survey(owner.tenantId(), folder);
        tree.archive(admin, folder);
        tree.archive(admin, project);

        ResourceView restored = tree.restore(admin, project);

        assertThat(restored.archivedAt()).isNull();
        assertThat(tree.get(admin, folder).archivedAt()).isNotNull();
        assertThat(tree.get(admin, survey).archivedAt()).isNull();
        assertThat(tree.list(admin, null, null, 20).items()).extracting(ResourceView::id).contains(project);
        assertThat(tree.list(admin, project, null, 20).items()).extracting(ResourceView::id).doesNotContain(folder);
        assertThat(tree.list(admin, project, null, 20, null, null, "archived", null).items())
                .extracting(ResourceView::id).containsExactly(folder);
    }

    @Test
    void archiveRequiresEditAndKeepsCrossTenantIdsPrivate() {
        UUID project = tree.createProject(admin, "项目").id();
        TenantContext viewer = fixture.member(owner, "viewer", "statistics_viewer", project);
        TenantContext otherOwner = fixture.newTenant();
        UUID otherProject = tree.createProject(otherOwner, "别的租户").id();

        assertThatThrownBy(() -> tree.archive(viewer, project))
                .isInstanceOf(ResourceAccessDeniedException.class);
        assertThatThrownBy(() -> tree.restore(viewer, project))
                .isInstanceOf(ResourceAccessDeniedException.class);
        assertThatThrownBy(() -> tree.archive(admin, otherProject))
                .isInstanceOf(ResourceNotFoundException.class);
        assertThatThrownBy(() -> tree.restore(admin, otherProject))
                .isInstanceOf(ResourceNotFoundException.class);
        assertThatThrownBy(() -> tree.archive(admin, UUID.randomUUID()))
                .isInstanceOf(ResourceNotFoundException.class);
        assertThatThrownBy(() -> tree.restore(admin, UUID.randomUUID()))
                .isInstanceOf(ResourceNotFoundException.class);
    }

    @Test
    void archiveAndRestoreAreAudited() {
        UUID project = tree.createProject(admin, "项目").id();

        ResourceView archived = tree.archive(admin, project);
        ResourceView archivedAgain = tree.archive(admin, project);
        ResourceView restored = tree.restore(admin, project);
        ResourceView restoredAgain = tree.restore(admin, project);

        assertThat(archivedAgain.archivedAt()).isEqualTo(archived.archivedAt());
        assertThat(restored.archivedAt()).isNull();
        assertThat(restoredAgain.archivedAt()).isNull();
        assertThat(auditCount(owner, "access.resource.archive", project)).isEqualTo(1);
        assertThat(auditCount(owner, "access.resource.restore", project)).isEqualTo(1);
    }

    @Test
    void capabilitiesDoNotRevealCrossTenantOrUnknownResources() {
        TenantContext otherOwner = fixture.newTenant();
        UUID otherProject = tree.createProject(otherOwner, "别的租户").id();

        assertThatThrownBy(() -> tree.capabilities(admin, otherProject))
                .isInstanceOf(ResourceNotFoundException.class);
        assertThatThrownBy(() -> tree.capabilities(admin, UUID.randomUUID()))
                .isInstanceOf(ResourceNotFoundException.class);
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

    private void setOrdering(UUID id, String name, Instant updatedAt) {
        transactions.executeWithoutResult(status -> {
            jdbc.sql("SELECT set_config('app.tenant_id', :t, true)")
                    .param("t", owner.tenantId().value().toString()).query(String.class).single();
            jdbc.sql("UPDATE access_resource SET name = :name, updated_at = :updated WHERE id = :id")
                    .param("name", Normalizer.normalize(name, Normalizer.Form.NFC))
                    .param("updated", Timestamp.from(updatedAt)).param("id", id).update();
        });
    }

    private void archive(UUID id) {
        transactions.executeWithoutResult(status -> {
            jdbc.sql("SELECT set_config('app.tenant_id', :t, true)")
                    .param("t", owner.tenantId().value().toString()).query(String.class).single();
            jdbc.sql("UPDATE access_resource SET archived_at = now() WHERE id = :id").param("id", id).update();
        });
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
