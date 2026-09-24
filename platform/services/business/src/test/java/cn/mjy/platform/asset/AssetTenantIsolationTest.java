package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 资产全部新表的跨租户负面测试（CONVENTIONS.md 硬性要求）：直接写裸 SQL、指名别的租户的 tenant_id，
 * 由数据库（行级安全 / 复合外键 / 最小授权）拒绝，而不是靠服务层判断。
 */
@SpringBootTest
class AssetTenantIsolationTest {

    private static final String[] TABLES = {"platform_asset", "platform_asset_version", "platform_asset_reference"};

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private AssetService assets;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantContext alice;
    private TenantContext bob;
    private AssetView aliceAsset;

    @BeforeEach
    void seed() {
        alice = fixture.newTenant();
        bob = fixture.newTenant();
        aliceAsset = assets.create(alice, "甲的封面", new AssetUpload("image/png", "a.png", AssetFixture.png()));
    }

    @Test
    void everyAssetTableEnforcesRowLevelSecurity() {
        for (String table : TABLES) {
            Boolean enabled = jdbc.sql("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = ?")
                    .param(table)
                    .query(Boolean.class)
                    .single();
            assertThat(enabled).as("%s 必须 ENABLE ＋ FORCE 行级安全", table).isTrue();
        }
    }

    @Test
    void bobCannotSeeAlicesRowsInAnyAssetTable() {
        for (String table : TABLES) {
            long visible = tenantScope.call(bob.tenantId(), () -> jdbc
                    .sql("SELECT count(*) FROM " + table + " WHERE tenant_id = '" + alice.tenantId().value() + "'")
                    .query(Long.class)
                    .single());
            assertThat(visible).as("%s 里甲的行对乙不可见", table).isZero();
        }
    }

    @Test
    void bobCannotInsertARowStampedWithAlicesTenantId() {
        assertThatThrownBy(() -> tenantScope.run(bob.tenantId(), () -> jdbc
                .sql("INSERT INTO platform_asset (tenant_id, id, kind, name, status, current_version,"
                        + " created_by, created_at, updated_at)"
                        + " VALUES (?, ?, 'image', '偷来的', 'active', 1, 'bob', now(), now())")
                .params(alice.tenantId().value(), UUID.randomUUID())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void bobCannotAttachAVersionToAlicesAsset() {
        assertThatThrownBy(() -> tenantScope.run(bob.tenantId(), () -> jdbc
                .sql("INSERT INTO platform_asset_version (tenant_id, asset_id, version_no, storage_key,"
                        + " content_type, byte_size, sha256, original_name, created_by, created_at)"
                        + " VALUES (?, ?, 9, 'asset/x/y/v9', 'image/png', 1,"
                        + " '0000000000000000000000000000000000000000000000000000000000000000', 'x.png',"
                        + " 'bob', now())")
                .params(bob.tenantId().value(), aliceAsset.id())
                .update()))
                // 复合外键带 tenant_id：乙的租户里根本没有这件资产。
                .rootCause().hasMessageContaining("foreign key");
    }

    /**
     * 版本行的内容不可改：改了就等于"已发布的问卷里那张图被换掉了"，而版本留存正是本服务存在的理由。
     * 删除权限保留——整件资产在无人引用时可以连同字节一起删掉（ADR 0019 决定 6）。
     */
    @Test
    void anAssetVersionsContentCanNeverBeUpdated() {
        assertThatThrownBy(() -> tenantScope.run(alice.tenantId(), () -> jdbc
                .sql("UPDATE platform_asset_version SET byte_size = 1 WHERE asset_id = ?")
                .param(aliceAsset.id())
                .update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    /** 引用行是"哪一版问卷用了哪一版资产"的证据，既不能改也不能删。 */
    @Test
    void assetReferenceRowsAreAppendOnlyForTheRuntimeAccount() {
        assertThatThrownBy(() -> tenantScope.run(alice.tenantId(), () -> jdbc
                .sql("UPDATE platform_asset_reference SET version_no = 9 WHERE asset_id = ?")
                .param(aliceAsset.id())
                .update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(alice.tenantId(), () -> jdbc
                .sql("DELETE FROM platform_asset_reference WHERE asset_id = ?")
                .param(aliceAsset.id())
                .update()))
                .rootCause().hasMessageContaining("permission denied");
    }
}
