package cn.mjy.platform.survey;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.AccessSettingsService;
import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.engine.EngineFixtures;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 问卷模块测试夹具：开通一个真实租户（引擎实例登记需要租户行）、设立所有者、登记一套 active 引擎实例、
 * 建一个项目。全部经各模块的公开服务方法完成。
 */
@Component
public class SurveyFixture {

    private static final String DEFINITION_RESOURCE = "/surveys/publish-gateway.json";

    private final TenantFixtures tenants;
    private final MemberService members;
    private final AccessFixture access;
    private final AccessSettingsService settings;
    private final EngineInstanceService engines;
    private final SurveyService surveys;
    private final JsonMapper json;

    public SurveyFixture(TenantFixtures tenants, MemberService members, AccessFixture access,
            AccessSettingsService settings, EngineInstanceService engines, SurveyService surveys, JsonMapper json) {
        this.tenants = tenants;
        this.members = members;
        this.access = access;
        this.settings = settings;
        this.engines = engines;
        this.surveys = surveys;
        this.json = json;
    }

    /** 一个租户的测试场景：所有者、项目、引擎实例。 */
    public record Workspace(TenantContext owner, UUID project, String engineInstanceId) {

        public TenantId tenant() {
            return owner.tenantId();
        }
    }

    public Workspace workspace() {
        Workspace bare = workspaceWithoutEngine();
        String instance = EngineFixtures.uniqueInstanceId();
        engines.register(bare.tenant(), instance, "https://engine.example/" + instance, "op", "trace");
        return new Workspace(bare.owner(), bare.project(), instance);
    }

    public Workspace workspaceWithoutEngine() {
        TenantId tenant = tenants.activeTenant();
        members.bootstrapOwner(tenant, AccessFixture.OWNER, "trace-bootstrap");
        TenantContext owner = AccessFixture.context(tenant, AccessFixture.OWNER);
        return new Workspace(owner, access.project(tenant), null);
    }

    public TenantContext member(Workspace ws, String actor, String role, UUID resource) {
        return access.member(ws.owner(), actor, role, resource);
    }

    public void requirePublishApproval(Workspace ws, boolean required) {
        settings.setPublishApprovalRequired(ws.owner(), required);
    }

    public SurveyView newSurvey(Workspace ws) {
        return surveys.create(ws.owner(), ws.project(), definition());
    }

    /** 样例定义加上一段 {@code policy}（契约 survey-access-policy-v1）。 */
    public SurveyView newSurveyWithPolicy(Workspace ws, String policyJson) {
        ObjectNode definition = definition();
        definition.set("policy", json.readTree(policyJson));
        return surveys.create(ws.owner(), ws.project(), definition);
    }

    /** 网关现有格式的样例定义（platform/tests/fixtures/surveys/publish-gateway.json 的副本）。 */
    public ObjectNode definition() {
        try (InputStream in = SurveyFixture.class.getResourceAsStream(DEFINITION_RESOURCE)) {
            if (in == null) {
                throw new IllegalStateException("missing test resource " + DEFINITION_RESOURCE);
            }
            return (ObjectNode) json.readTree(in);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public ObjectNode definitionTitled(String title) {
        ObjectNode definition = definition();
        definition.put("title", title);
        return definition;
    }

    public JsonNode parse(String text) {
        return json.readTree(text);
    }
}
