package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * delivery_link 的读写。必须在 {@code TenantScope} 内调用，可见范围由行级安全决定。
 * 短链码来自控制平面登记表 delivery_short_link（见 V700 的说明），按链接 id 左连接读出。
 */
@Repository
class DeliveryLinkRepository {

    private static final String COLUMNS = """
            l.id, l.survey_id, l.label, l.signed_params::text AS signed_params,
            l.expires_at, l.revoked_at, l.created_at, s.code AS short_code
            FROM delivery_link l
            LEFT JOIN delivery_short_link s ON s.tenant_id = l.tenant_id AND s.link_id = l.id""";

    private static final TypeReference<Map<String, String>> PARAMS = new TypeReference<>() {
    };

    /** 一条链接的存储形态；作答地址与短链地址由服务层现算，不落库。 */
    record LinkRow(
            UUID id,
            UUID surveyId,
            String label,
            Map<String, String> signedParams,
            String shortCode,
            OffsetDateTime expiresAt,
            OffsetDateTime revokedAt,
            OffsetDateTime createdAt) {
    }

    private final JdbcClient jdbc;
    private final JsonMapper json;

    DeliveryLinkRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    void insert(TenantId tenant, UUID id, UUID surveyId, String label, Map<String, String> signedParams,
            OffsetDateTime expiresAt, String createdBy) {
        jdbc.sql("""
                        INSERT INTO delivery_link (tenant_id, id, survey_id, label, signed_params,
                                                   expires_at, created_by)
                        VALUES (:tenant, :id, :survey, :label, CAST(:params AS jsonb), :expires, :createdBy)
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("survey", surveyId)
                .param("label", label)
                .param("params", json.writeValueAsString(signedParams))
                .param("expires", expiresAt)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<LinkRow> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " WHERE l.id = :id")
                .param("id", id)
                .query(this::map)
                .optional();
    }

    List<LinkRow> listBySurvey(UUID surveyId, int limit) {
        return jdbc.sql("SELECT " + COLUMNS + " WHERE l.survey_id = :survey ORDER BY l.created_at DESC LIMIT :limit")
                .param("survey", surveyId)
                .param("limit", limit)
                .query(this::map)
                .list();
    }

    /** 作废：已作废的链接再作废一次不改时刻（幂等）。 */
    boolean revoke(UUID id) {
        return jdbc.sql("UPDATE delivery_link SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL")
                .param("id", id)
                .update() == 1;
    }

    /** 登记短链码；码已被占用（概率极低）时返回 false，由调用方换一个再试。 */
    boolean registerShortCode(TenantId tenant, UUID linkId, String code) {
        return jdbc.sql("""
                        INSERT INTO delivery_short_link (code, tenant_id, link_id)
                        VALUES (:code, :tenant, :link)
                        ON CONFLICT DO NOTHING
                        """)
                .param("code", code)
                .param("tenant", tenant.value())
                .param("link", linkId)
                .update() == 1;
    }

    /**
     * 短链码 → (租户, 链接)。这是控制平面查询，在租户作用域之外执行——匿名请求此刻还不知道是哪个租户。
     * 只返回标识，链接内容仍要进入该租户的作用域才能读到。
     */
    Optional<ShortLinkTarget> resolveShortCode(String code) {
        return jdbc.sql("SELECT tenant_id, link_id FROM delivery_short_link WHERE code = :code")
                .param("code", code)
                .query((rs, n) -> new ShortLinkTarget(
                        new TenantId(rs.getObject("tenant_id", UUID.class)),
                        rs.getObject("link_id", UUID.class)))
                .optional();
    }

    /** 短链登记里唯一的信息：这条短链属于哪个租户的哪条链接。 */
    record ShortLinkTarget(TenantId tenant, UUID linkId) {
    }

    private LinkRow map(ResultSet rs, int rowNum) throws SQLException {
        return new LinkRow(
                rs.getObject("id", UUID.class),
                rs.getObject("survey_id", UUID.class),
                rs.getString("label"),
                json.readValue(rs.getString("signed_params"), PARAMS),
                rs.getString("short_code"),
                rs.getObject("expires_at", OffsetDateTime.class),
                rs.getObject("revoked_at", OffsetDateTime.class),
                rs.getObject("created_at", OffsetDateTime.class));
    }
}
