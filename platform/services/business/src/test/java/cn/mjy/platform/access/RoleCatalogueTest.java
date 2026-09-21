package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 角色目录是数据：角色 -> 权限集合来自数据库表，运行期账号不能改。 */
@SpringBootTest
class RoleCatalogueTest {

    @Autowired
    private RoleCatalogue catalogue;

    @Autowired
    private JdbcClient jdbc;

    @Test
    void everyTenantRoleFromThePlanIsInTheCatalogue() {
        assertThat(catalogue.all()).extracting(RoleDefinition::code).containsExactlyInAnyOrder(
                "tenant_owner", "finance", "org_admin", "project_manager", "editor", "publish_reviewer",
                "raw_data_viewer", "statistics_viewer", "grader", "interviewer", "participant", "support");
    }

    @Test
    void thePermissionEnumMatchesThePermissionTableExactly() {
        List<String> inDatabase = jdbc.sql("SELECT code FROM access_permission").query(String.class).list();

        assertThat(inDatabase).containsExactlyInAnyOrderElementsOf(
                Arrays.stream(Permission.values()).map(Permission::code).toList());
    }

    @Test
    void theStatisticsViewerHoldsNoRawResponsePermission() {
        RoleDefinition viewer = catalogue.find("statistics_viewer").orElseThrow();

        assertThat(viewer.permissions()).contains(Permission.VIEW_STATISTICS)
                .doesNotContain(Permission.VIEW_RAW_RESPONSES, Permission.EXPORT_RAW_RESPONSES,
                        Permission.VIEW_SENSITIVE_FIELDS);
    }

    @Test
    void onlyAdministratorsMayPublishWithoutApproval() {
        List<String> bypass = catalogue.all().stream()
                .filter(role -> role.permissions().contains(Permission.PUBLISH_WITHOUT_APPROVAL))
                .map(RoleDefinition::code)
                .toList();

        assertThat(bypass).containsExactlyInAnyOrder("tenant_owner", "org_admin");
    }

    @Test
    void supportAccessMustBeTimeLimited() {
        assertThat(catalogue.find("support").orElseThrow().maxGrantHours()).isPositive();
    }

    @Test
    void theRuntimeAccountCannotRewriteTheCatalogue() {
        assertThatThrownBy(() -> jdbc.sql(
                "INSERT INTO access_role_permission (role_code, permission_code) VALUES ('statistics_viewer', 'export-raw-responses')")
                .update()).rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> jdbc.sql("UPDATE access_role SET requires_seat = false").update())
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> jdbc.sql("DELETE FROM access_permission").update())
                .rootCause().hasMessageContaining("permission denied");
    }
}
