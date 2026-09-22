package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 资源树的数据库兜底（V402），用原始 SQL 模拟绕过服务层的代码缺陷：
 * 移动后的父节点只能是本租户的项目 / 文件夹、不能成环、不能改类型或租户；
 * 别的租户的行既改不到也挂不上。
 */
@SpringBootTest
class ResourceTreeDatabaseGuardsTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;
    private UUID projectA;
    private UUID parentA;
    private UUID childA;
    private UUID surveyA;
    private UUID projectB;

    @BeforeEach
    void trees() {
        TenantContext ownerA = fixture.newTenant();
        TenantContext ownerB = fixture.newTenant();
        tenantA = ownerA.tenantId();
        tenantB = ownerB.tenantId();
        projectA = fixture.project(tenantA);
        parentA = fixture.folder(tenantA, projectA);
        childA = fixture.folder(tenantA, parentA);
        surveyA = fixture.survey(tenantA, projectA);
        projectB = fixture.project(tenantB);
    }

    @Test
    void tenantBCannotReparentTenantANodes() {
        int updated = tenantScope.call(tenantB, () -> jdbc.sql(
                        "UPDATE access_resource SET parent_id = :p WHERE tenant_id = :a AND id = :id")
                .param("p", projectB).param("a", tenantA.value()).param("id", parentA).update());

        assertThat(updated).isZero();
        assertThat(parentOf(tenantA, parentA)).isEqualTo(projectA);
    }

    @Test
    void aNodeCannotBeReparentedUnderAnotherTenantsNode() {
        assertThatThrownBy(() -> reparent(tenantA, parentA, projectB)).isInstanceOf(DataAccessException.class);
    }

    @Test
    void theDatabaseRejectsACycleEvenWhenTheServiceIsBypassed() {
        assertThatThrownBy(() -> reparent(tenantA, parentA, childA)).isInstanceOf(DataAccessException.class);
        assertThatThrownBy(() -> reparent(tenantA, parentA, parentA)).isInstanceOf(DataAccessException.class);
        assertThat(parentOf(tenantA, parentA)).isEqualTo(projectA);
    }

    @Test
    void theDatabaseRejectsASurveyAsAParent() {
        assertThatThrownBy(() -> reparent(tenantA, childA, surveyA)).isInstanceOf(DataAccessException.class);
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc.sql("""
                        INSERT INTO access_resource (tenant_id, id, kind, parent_id, name)
                        VALUES (:t, :id, 'folder', :p, 'x')""")
                .param("t", tenantA.value()).param("id", UUID.randomUUID()).param("p", surveyA).update()))
                .isInstanceOf(DataAccessException.class);
    }

    @Test
    void kindAndTenantOfANodeAreImmutable() {
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc.sql(
                        "UPDATE access_resource SET kind = 'survey' WHERE id = :id").param("id", parentA).update()))
                .isInstanceOf(DataAccessException.class);
    }

    @Test
    void aLegalReparentStillWorksAtTheDatabaseLevel() {
        UUID otherFolder = fixture.folder(tenantA, projectA);

        reparent(tenantA, childA, otherFolder);

        assertThat(parentOf(tenantA, childA)).isEqualTo(otherFolder);
    }

    private void reparent(TenantId tenant, UUID node, UUID parent) {
        tenantScope.run(tenant, () -> jdbc.sql("UPDATE access_resource SET parent_id = :p WHERE id = :id")
                .param("p", parent).param("id", node).update());
    }

    private UUID parentOf(TenantId tenant, UUID node) {
        return tenantScope.call(tenant, () -> jdbc.sql("SELECT parent_id FROM access_resource WHERE id = :id")
                .param("id", node).query(UUID.class).single());
    }
}
