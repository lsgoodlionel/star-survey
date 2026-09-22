package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * survey_drift_check 的读写（只追加：运行期账号没有 UPDATE/DELETE）。必须在 {@code TenantScope} 内调用。
 */
@Repository
class DriftCheckRepository {

    private static final String COLUMNS = """
            id, survey_id, version_no, engine_instance_id, engine_sid, expected_fingerprint, current_fingerprint,
            outcome, issues::text AS issues, renamed::text AS renamed, detail, checked_by, checked_at""";

    private final JdbcClient jdbc;
    private final JsonMapper json;

    DriftCheckRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** 被检查的对象：某问卷某版本的引擎位置、期望指纹与绑定原文。 */
    record Target(UUID surveyId, int versionNo, String engineInstanceId, int engineSid, String fingerprint,
            String bindingJson) {
    }

    /** 一次检查的结论（写入前）。 */
    record Finding(String outcome, String currentFingerprint, List<DriftCheckView.Issue> issues,
            List<DriftCheckView.Rename> renamed, String detail) {
    }

    long insert(TenantId tenant, Target target, Finding finding, String checkedBy) {
        return jdbc.sql("""
                        INSERT INTO survey_drift_check (tenant_id, survey_id, version_no, engine_instance_id, engine_sid,
                            expected_fingerprint, current_fingerprint, outcome, issues, renamed, detail, checked_by)
                        VALUES (:tenant, :survey, :version, :instance, :sid, :expected, :current, :outcome,
                            CAST(:issues AS jsonb), CAST(:renamed AS jsonb), :detail, :checkedBy)
                        RETURNING id
                        """)
                .param("tenant", tenant.value())
                .param("survey", target.surveyId())
                .param("version", target.versionNo())
                .param("instance", target.engineInstanceId())
                .param("sid", target.engineSid())
                .param("expected", target.fingerprint())
                .param("current", finding.currentFingerprint(), java.sql.Types.VARCHAR)
                .param("outcome", finding.outcome())
                .param("issues", json.writeValueAsString(finding.issues()))
                .param("renamed", json.writeValueAsString(finding.renamed()))
                .param("detail", finding.detail(), java.sql.Types.VARCHAR)
                .param("checkedBy", checkedBy)
                .query(Long.class)
                .single();
    }

    Optional<DriftCheckView> find(long id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_drift_check WHERE id = :id")
                .param("id", id)
                .query(this::map)
                .optional();
    }

    /** 某版本最近的检查，新的在前。 */
    List<DriftCheckView> list(UUID surveyId, int versionNo, int limit) {
        return jdbc.sql("SELECT " + COLUMNS + """
                         FROM survey_drift_check WHERE survey_id = :survey AND version_no = :version
                        ORDER BY id DESC LIMIT :limit
                        """)
                .param("survey", surveyId)
                .param("version", versionNo)
                .param("limit", limit)
                .query(this::map)
                .list();
    }

    /** 某问卷某版本的检查对象；版本不存在时为空。 */
    Optional<Target> target(UUID surveyId, int versionNo) {
        return jdbc.sql("""
                        SELECT survey_id, version_no, engine_instance_id, engine_sid, fingerprint, binding::text AS binding
                          FROM survey_published_version WHERE survey_id = :survey AND version_no = :version
                        """)
                .param("survey", surveyId)
                .param("version", versionNo)
                .query(DriftCheckRepository::mapTarget)
                .optional();
    }

    /** 本租户在线版本里，最近 interval 内没有检查过的，从未检查或最久未检查的在前（定时巡检用）。 */
    List<Target> liveVersionsDue(Duration interval, int limit) {
        return jdbc.sql("""
                        SELECT v.survey_id, v.version_no, v.engine_instance_id, v.engine_sid, v.fingerprint,
                               v.binding::text AS binding
                          FROM survey s
                          JOIN survey_published_version v
                            ON v.tenant_id = s.tenant_id AND v.survey_id = s.id AND v.version_no = s.published_version
                          LEFT JOIN LATERAL (
                                SELECT max(c.checked_at) AS last_checked FROM survey_drift_check c
                                 WHERE c.tenant_id = v.tenant_id AND c.survey_id = v.survey_id
                                   AND c.version_no = v.version_no) last ON true
                         WHERE last.last_checked IS NULL OR last.last_checked < now() - make_interval(secs => :interval)
                         ORDER BY last.last_checked NULLS FIRST, v.survey_id
                         LIMIT :limit
                        """)
                .param("interval", (double) interval.toSeconds())
                .param("limit", limit)
                .query(DriftCheckRepository::mapTarget)
                .list();
    }

    private static Target mapTarget(ResultSet rs, int row) throws SQLException {
        return new Target(
                rs.getObject("survey_id", UUID.class),
                rs.getInt("version_no"),
                rs.getString("engine_instance_id"),
                rs.getInt("engine_sid"),
                rs.getString("fingerprint"),
                rs.getString("binding"));
    }

    private DriftCheckView map(ResultSet rs, int row) throws SQLException {
        return new DriftCheckView(
                rs.getLong("id"),
                rs.getObject("survey_id", UUID.class),
                rs.getInt("version_no"),
                rs.getString("engine_instance_id"),
                rs.getInt("engine_sid"),
                rs.getString("expected_fingerprint"),
                rs.getString("current_fingerprint"),
                rs.getString("outcome"),
                issues(rs.getString("issues")),
                renamed(rs.getString("renamed")),
                rs.getString("detail"),
                rs.getString("checked_by"),
                rs.getObject("checked_at", OffsetDateTime.class));
    }

    private List<DriftCheckView.Issue> issues(String array) {
        List<DriftCheckView.Issue> values = new ArrayList<>();
        for (JsonNode item : json.readTree(array)) {
            values.add(new DriftCheckView.Issue(item.get("code").asString(), text(item, "detail")));
        }
        return values;
    }

    private List<DriftCheckView.Rename> renamed(String array) {
        List<DriftCheckView.Rename> values = new ArrayList<>();
        for (JsonNode item : json.readTree(array)) {
            values.add(new DriftCheckView.Rename(text(item, "questionUuid"), text(item, "from"), text(item, "to")));
        }
        return values;
    }

    private static String text(JsonNode node, String name) {
        JsonNode value = node.get(name);
        return value == null || value.isNull() ? null : value.asString();
    }
}
