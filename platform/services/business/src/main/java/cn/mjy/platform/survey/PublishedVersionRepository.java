package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.survey.PublishedVersionView.QuestionFieldView;
import cn.mjy.platform.survey.gateway.GatewayBinding;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.json.JsonMapper;

/**
 * 已发布版本与题目映射的读写（只追加：运行期账号对两张表没有 UPDATE/DELETE）。
 * 必须在 {@code TenantScope} 内调用。
 */
@Repository
class PublishedVersionRepository {

    /** 版本行本身不可改；在线与否、何时被取代、引擎何时收口来自 survey 与 survey_version_retirement（V530）。 */
    private static final String COLUMNS = """
            v.survey_id, v.version_no, v.request_id, v.draft_version, v.engine_instance_id, v.engine_sid,
            v.compiler_version, v.fingerprint_version, v.fingerprint, v.language, v.engine_published_at,
            v.published_by, v.published_at, v.definition::text AS definition,
            (s.published_version IS NOT DISTINCT FROM v.version_no) AS live,
            r.superseded_at, r.engine_closed_at
            FROM survey_published_version v
            JOIN survey s ON s.tenant_id = v.tenant_id AND s.id = v.survey_id
            LEFT JOIN survey_version_retirement r
              ON r.tenant_id = v.tenant_id AND r.survey_id = v.survey_id AND r.version_no = v.version_no""";

    private final JdbcClient jdbc;
    private final JsonMapper json;

    PublishedVersionRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    /** 新版本：调用方持有问卷行锁，因此"最大版本号 + 1"不会并发冲突。 */
    int insert(TenantId tenant, UUID surveyId, UUID requestId, int draftVersion, String definition,
            GatewayBinding binding, String publishedBy) {
        int versionNo = jdbc.sql("""
                        SELECT COALESCE(MAX(version_no), 0) + 1 FROM survey_published_version WHERE survey_id = :survey
                        """)
                .param("survey", surveyId)
                .query(Integer.class)
                .single();
        jdbc.sql("""
                        INSERT INTO survey_published_version (tenant_id, survey_id, version_no, request_id, draft_version,
                            engine_instance_id, engine_sid, definition, compiler_version, fingerprint_version,
                            fingerprint, language, engine_published_at, binding, published_by)
                        VALUES (:tenant, :survey, :version, :request, :draftVersion, :instance, :sid,
                            CAST(:definition AS jsonb), :compiler, :fingerprintVersion, :fingerprint, :language,
                            :enginePublishedAt, CAST(:binding AS jsonb), :publishedBy)
                        """)
                .param("tenant", tenant.value())
                .param("survey", surveyId)
                .param("version", versionNo)
                .param("request", requestId)
                .param("draftVersion", draftVersion)
                .param("instance", binding.engineInstance())
                .param("sid", binding.surveyId())
                .param("definition", definition)
                .param("compiler", binding.compilerVersion())
                .param("fingerprintVersion", binding.fingerprintVersion())
                .param("fingerprint", binding.fingerprint())
                .param("language", binding.language())
                .param("enginePublishedAt", binding.publishedAt())
                .param("binding", json.writeValueAsString(binding))
                .param("publishedBy", publishedBy)
                .update();
        insertFields(tenant, surveyId, versionNo, binding);
        return versionNo;
    }

    private void insertFields(TenantId tenant, UUID surveyId, int versionNo, GatewayBinding binding) {
        int ordinal = 0;
        for (GatewayBinding.QuestionBinding question : binding.questions()) {
            for (GatewayBinding.FieldBinding field : question.fields()) {
                jdbc.sql("""
                                INSERT INTO survey_question_binding (tenant_id, survey_id, version_no, ordinal,
                                    question_uuid, question_code, question_type, fieldname, aid, scale)
                                VALUES (:tenant, :survey, :version, :ordinal, :question, :code, :type, :field, :aid, :scale)
                                """)
                        .param("tenant", tenant.value())
                        .param("survey", surveyId)
                        .param("version", versionNo)
                        .param("ordinal", ordinal++)
                        .param("question", UUID.fromString(question.uuid()))
                        .param("code", question.code())
                        .param("type", question.type())
                        .param("field", field.fieldname())
                        .param("aid", field.aid())
                        .param("scale", field.scale())
                        .update();
            }
        }
    }

    List<PublishedVersionView> list(UUID surveyId) {
        return jdbc.sql("SELECT " + COLUMNS + " WHERE v.survey_id = :survey ORDER BY v.version_no")
                .param("survey", surveyId)
                .query((rs, n) -> map(rs))
                .list();
    }

    Optional<PublishedVersionView> find(UUID surveyId, int versionNo) {
        return jdbc.sql("SELECT " + COLUMNS + " WHERE v.survey_id = :survey AND v.version_no = :version")
                .param("survey", surveyId)
                .param("version", versionNo)
                .query((rs, n) -> map(rs))
                .optional();
    }

    private PublishedVersionView map(ResultSet rs) throws SQLException {
        UUID surveyId = rs.getObject("survey_id", UUID.class);
        int versionNo = rs.getInt("version_no");
        return new PublishedVersionView(
                surveyId,
                versionNo,
                rs.getObject("request_id", UUID.class),
                rs.getInt("draft_version"),
                rs.getString("engine_instance_id"),
                rs.getInt("engine_sid"),
                rs.getString("compiler_version"),
                rs.getString("fingerprint_version"),
                rs.getString("fingerprint"),
                rs.getString("language"),
                rs.getString("engine_published_at"),
                rs.getString("published_by"),
                rs.getObject("published_at", OffsetDateTime.class),
                fields(surveyId, versionNo),
                json.readTree(rs.getString("definition")),
                rs.getBoolean("live"),
                rs.getObject("superseded_at", OffsetDateTime.class),
                rs.getObject("engine_closed_at", OffsetDateTime.class));
    }

    /** 网关当时返回的绑定记录原文（漂移检查时原样交回网关）。 */
    Optional<String> bindingJson(UUID surveyId, int versionNo) {
        return jdbc.sql("""
                        SELECT binding::text FROM survey_published_version
                         WHERE survey_id = :survey AND version_no = :version
                        """)
                .param("survey", surveyId)
                .param("version", versionNo)
                .query(String.class)
                .optional();
    }

    private List<QuestionFieldView> fields(UUID surveyId, int versionNo) {
        return jdbc.sql("""
                        SELECT question_uuid, question_code, question_type, fieldname, aid, scale
                          FROM survey_question_binding
                         WHERE survey_id = :survey AND version_no = :version
                         ORDER BY ordinal
                        """)
                .param("survey", surveyId)
                .param("version", versionNo)
                .query((rs, n) -> new QuestionFieldView(
                        rs.getObject("question_uuid", UUID.class),
                        rs.getString("question_code"),
                        rs.getString("question_type"),
                        rs.getString("fieldname"),
                        rs.getString("aid"),
                        rs.getInt("scale")))
                .list();
    }
}
