package cn.mjy.platform.asset;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 资产元数据的读写。须在 {@code TenantScope} 内调用——这里一条 SQL 都不带 tenant_id 条件，
 * 隔离完全由行级安全负责（写入用 {@code app_current_tenant()}，越权写会被策略拒绝而不是被这里的判断拒绝）。
 */
@Repository
class AssetRepository {

    private static final String ASSET_COLUMNS =
            "id, name, kind, status, current_version, created_at, updated_at";
    private static final String VERSION_COLUMNS =
            "version_no, content_type, byte_size, sha256, original_name, storage_key, created_at";

    private final JdbcClient jdbc;

    AssetRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 资产行（不含版本元数据）。 */
    record AssetRow(UUID id, String name, AssetKind kind, AssetStatus status, int currentVersion,
            Instant createdAt, Instant updatedAt) {
    }

    void insertAsset(UUID id, String name, AssetKind kind, String createdBy) {
        jdbc.sql("""
                        INSERT INTO platform_asset (tenant_id, id, kind, name, status, current_version, created_by)
                        VALUES (app_current_tenant(), :id, :kind, :name, 'active', 1, :by)
                        """)
                .param("id", id).param("kind", kind.code()).param("name", name).param("by", createdBy)
                .update();
    }

    void insertVersion(UUID assetId, int versionNo, String storageKey, String contentType, long byteSize,
            String sha256, String originalName, String createdBy) {
        jdbc.sql("""
                        INSERT INTO platform_asset_version (tenant_id, asset_id, version_no, storage_key,
                            content_type, byte_size, sha256, original_name, created_by)
                        VALUES (app_current_tenant(), :asset, :no, :key, :type, :size, :sha, :name, :by)
                        """)
                .param("asset", assetId).param("no", versionNo).param("key", storageKey).param("type", contentType)
                .param("size", byteSize).param("sha", sha256).param("name", originalName).param("by", createdBy)
                .update();
    }

    /** 记下新版本号并前移当前版本；返回新的版本号。行锁保证并发上传不会撞号。 */
    int nextVersion(UUID assetId) {
        return jdbc.sql("""
                        UPDATE platform_asset SET current_version = current_version + 1, updated_at = now()
                        WHERE id = :id
                        RETURNING current_version
                        """)
                .param("id", assetId).query(Integer.class).single();
    }

    Optional<AssetRow> findAsset(UUID id) {
        return jdbc.sql("SELECT " + ASSET_COLUMNS + " FROM platform_asset WHERE id = :id FOR UPDATE")
                .param("id", id).query(AssetRepository::toAsset).optional();
    }

    Optional<AssetRow> readAsset(UUID id) {
        return jdbc.sql("SELECT " + ASSET_COLUMNS + " FROM platform_asset WHERE id = :id")
                .param("id", id).query(AssetRepository::toAsset).optional();
    }

    List<AssetRow> listActive(int limit) {
        return jdbc.sql("SELECT " + ASSET_COLUMNS + " FROM platform_asset WHERE status = 'active'"
                        + " ORDER BY created_at DESC, id DESC LIMIT :limit")
                .param("limit", limit).query(AssetRepository::toAsset).list();
    }

    Optional<AssetVersionView> findVersion(UUID assetId, int versionNo) {
        return jdbc.sql("SELECT " + VERSION_COLUMNS + " FROM platform_asset_version"
                        + " WHERE asset_id = :asset AND version_no = :no")
                .param("asset", assetId).param("no", versionNo).query(AssetRepository::toVersion).optional();
    }

    List<AssetVersionView> listVersions(UUID assetId) {
        return jdbc.sql("SELECT " + VERSION_COLUMNS + " FROM platform_asset_version"
                        + " WHERE asset_id = :asset ORDER BY version_no")
                .param("asset", assetId).query(AssetRepository::toVersion).list();
    }

    void updateStatus(UUID id, AssetStatus status) {
        jdbc.sql("UPDATE platform_asset SET status = :status, updated_at = now() WHERE id = :id")
                .param("status", status.code()).param("id", id).update();
    }

    /** 删除资产行；版本行按外键级联删除。引用行不级联——有引用时外键会挡住，是删除守卫的第二道。 */
    void deleteAsset(UUID id) {
        jdbc.sql("DELETE FROM platform_asset WHERE id = :id").param("id", id).update();
    }

    boolean hasReferences(UUID assetId) {
        return Boolean.TRUE.equals(jdbc
                .sql("SELECT EXISTS (SELECT 1 FROM platform_asset_reference WHERE asset_id = :asset)")
                .param("asset", assetId).query(Boolean.class).single());
    }

    /** 登记一次引用；同一份快照重复登记是幂等的（核对重发会走到这里）。 */
    void insertReference(UUID assetId, int versionNo, UUID surveyId, int draftVersion) {
        jdbc.sql("""
                        INSERT INTO platform_asset_reference
                            (tenant_id, asset_id, version_no, survey_id, draft_version)
                        VALUES (app_current_tenant(), :asset, :no, :survey, :draft)
                        ON CONFLICT DO NOTHING
                        """)
                .param("asset", assetId).param("no", versionNo).param("survey", surveyId).param("draft", draftVersion)
                .update();
    }

    private static AssetRow toAsset(ResultSet rs, int row) throws SQLException {
        return new AssetRow(rs.getObject("id", UUID.class), rs.getString("name"),
                AssetKind.fromCode(rs.getString("kind")).orElseThrow(),
                AssetStatus.fromCode(rs.getString("status")).orElseThrow(),
                rs.getInt("current_version"),
                rs.getObject("created_at", OffsetDateTime.class).toInstant(),
                rs.getObject("updated_at", OffsetDateTime.class).toInstant());
    }

    private static AssetVersionView toVersion(ResultSet rs, int row) throws SQLException {
        return new AssetVersionView(rs.getInt("version_no"), rs.getString("content_type"), rs.getLong("byte_size"),
                rs.getString("sha256"), rs.getString("original_name"), rs.getString("storage_key"),
                rs.getObject("created_at", OffsetDateTime.class).toInstant());
    }
}
