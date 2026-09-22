package cn.mjy.platform.response;

import cn.mjy.platform.engine.EngineEvent;
import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.engine.ResponseProjectionRepository;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.PublishedVersionView;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyPublishService;
import cn.mjy.platform.survey.SurveyService;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 答卷查询的测试场景：一个已发布的问卷（QTEXT 标为敏感）、按需追加的第二个版本（另一个 sid），
 * 以及按引擎事件推动的答卷投影。发布走真实发布流程（网关替身），第二个版本因"重新发布"尚未实现而直接写表。
 */
@Component
public class ResponseFixture {

    /** 样例定义里 QTEXT 是第二道题，网关替身给它的列名固定为 Q101。 */
    public static final String SENSITIVE_FIELD = "Q101";
    public static final String PLAIN_FIELD = "Q100";
    public static final String GENERATION = "gen-a";

    private static final AtomicInteger NEXT_SID = new AtomicInteger(880_000);

    private final SurveyFixture surveys;
    private final SurveyService surveyService;
    private final SurveyPublishService publisher;
    private final ResponseProjectionRepository projections;
    private final TenantScope tenantScope;
    private final JdbcClient jdbc;
    private final JsonMapper json;

    public ResponseFixture(SurveyFixture surveys, SurveyService surveyService, SurveyPublishService publisher,
            ResponseProjectionRepository projections, TenantScope tenantScope, JdbcClient jdbc, JsonMapper json) {
        this.surveys = surveys;
        this.surveyService = surveyService;
        this.publisher = publisher;
        this.projections = projections;
        this.tenantScope = tenantScope;
        this.jdbc = jdbc;
        this.json = json;
    }

    /** 一个租户、一份已发布的问卷。 */
    public record Published(Workspace ws, UUID surveyId, PublishedVersionView v1) {

        public TenantContext owner() {
            return ws.owner();
        }

        public TenantId tenant() {
            return ws.tenant();
        }

        public String instance() {
            return v1.engineInstanceId();
        }

        public long sid() {
            return v1.engineSid();
        }
    }

    public Published publishedSurvey() {
        Workspace ws = surveys.workspace();
        UUID id = surveyService.create(ws.owner(), ws.project(), sensitiveDefinition()).id();
        PublishedVersionView v1 = publisher.publish(ws.owner(), id).version();
        return new Published(ws, id, v1);
    }

    public ObjectNode sensitiveDefinition() {
        ObjectNode definition = surveys.definition();
        ObjectNode qtext = (ObjectNode) definition.get("groups").get(0).get("questions").get(1);
        qtext.put("sensitive", true);
        return definition;
    }

    public TenantContext member(Published p, String actor, String role) {
        return surveys.member(p.ws(), actor, role, p.surveyId());
    }

    /**
     * 直接写入第二个已发布版本（另一个 sid、另一套列名），模拟重新发布的结果。
     * 运行期账号对这两张表只有 INSERT，写在租户作用域内，行级安全照常生效。
     */
    public long addVersion(Published p, List<String> fieldnames) {
        long sid = NEXT_SID.incrementAndGet();
        UUID requestId = UUID.randomUUID();
        String definition = json.writeValueAsString(sensitiveDefinition().put("uuid", p.surveyId().toString()));
        tenantScope.run(p.tenant(), () -> {
            jdbc.sql("""
                    INSERT INTO survey_publish_attempt (tenant_id, request_id, survey_id, draft_version,
                        engine_instance_id, definition, outcome, requested_by, completed_at)
                    VALUES (:tenant, :request, :survey, 1, :instance, CAST(:definition AS jsonb), 'published', 'owner', now())
                    """)
                    .param("tenant", p.tenant().value()).param("request", requestId).param("survey", p.surveyId())
                    .param("instance", p.instance()).param("definition", definition).update();
            int version = jdbc.sql("SELECT MAX(version_no) + 1 FROM survey_published_version WHERE survey_id = :s")
                    .param("s", p.surveyId()).query(Integer.class).single();
            jdbc.sql("""
                    INSERT INTO survey_published_version (tenant_id, survey_id, version_no, request_id, draft_version,
                        engine_instance_id, engine_sid, definition, compiler_version, fingerprint_version, fingerprint,
                        language, engine_published_at, binding, published_by)
                    VALUES (:tenant, :survey, :version, :request, 1, :instance, :sid, CAST(:definition AS jsonb),
                        'pubgw-lss-1', 'fm1', 'fm1:0000000000000000', 'en', '2026-09-22T09:00:00Z', '{}'::jsonb, 'owner')
                    """)
                    .param("tenant", p.tenant().value()).param("survey", p.surveyId()).param("version", version)
                    .param("request", requestId).param("instance", p.instance()).param("sid", sid)
                    .param("definition", definition).update();
            String[] codes = {"QSINGLE", "QTEXT", "QNOTE", "QMULTI", "QDUAL"};
            String[] uuids = {"33333333-0001-4111-8111-000000000001", "33333333-0002-4111-8111-000000000002",
                    "33333333-0003-4111-8111-000000000003", "33333333-0004-4111-8111-000000000004",
                    "33333333-0005-4111-8111-000000000005"};
            for (int i = 0; i < fieldnames.size(); i++) {
                jdbc.sql("""
                        INSERT INTO survey_question_binding (tenant_id, survey_id, version_no, ordinal, question_uuid,
                            question_code, question_type, fieldname, aid, scale)
                        VALUES (:tenant, :survey, :version, :ordinal, :uuid, :code, 'S', :field, '', 0)
                        """)
                        .param("tenant", p.tenant().value()).param("survey", p.surveyId()).param("version", version)
                        .param("ordinal", i).param("uuid", UUID.fromString(uuids[i])).param("code", codes[i])
                        .param("field", fieldnames.get(i)).update();
            }
        });
        return sid;
    }

    /** 让一份答卷走到指定状态（依次经过保存、完成、删除事件）。 */
    public void response(TenantId tenant, String instance, long sid, String generation, long responseId,
            EngineEventType upTo) {
        for (EngineEventType type : EngineEventType.values()) {
            event(tenant, instance, sid, generation, responseId, type, Instant.now());
            if (type == upTo) {
                return;
            }
        }
    }

    public void event(TenantId tenant, String instance, long sid, String generation, long responseId,
            EngineEventType type, Instant occurredAt) {
        EngineEvent event = new EngineEvent(UUID.randomUUID(), type, instance, sid, generation, responseId,
                "hook", occurredAt);
        tenantScope.run(tenant, () -> projections.advance(tenant, event));
    }
}
