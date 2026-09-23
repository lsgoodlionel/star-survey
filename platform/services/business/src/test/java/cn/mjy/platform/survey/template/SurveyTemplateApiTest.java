package cn.mjy.platform.survey.template;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.tenant.PlatformRoles;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.json.JsonMapper;

/**
 * HTTP 层：租户模板与运营模板的接口、跨租户 404、运营端点必须带运营角色。
 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyTemplateApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private Workspace ws;
    private TenantContext reviewer;
    private UUID surveyId;

    @BeforeEach
    void aWorkspaceWithASurvey() {
        ws = fixture.workspace();
        reviewer = fixture.member(ws, "sub-reviewer", "publish_reviewer", null);
        surveyId = fixture.newSurvey(ws).id();
    }

    @Test
    void theWholeLoopOverHttp() throws Exception {
        String created = send(post("/v1/survey-templates"), ws.owner(),
                Map.of("sourceSurveyId", surveyId.toString(), "name", "标准满意度", "description", "给新项目用"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("draft"))
                .andExpect(jsonPath("$.scope").value("tenant"))
                .andExpect(jsonPath("$.currentVersion").value(1))
                .andReturn().getResponse().getContentAsString();
        String id = JsonPath.read(created, "$.id");

        // 未审核：库里看不到。
        send(get("/v1/survey-templates?scope=tenant"), ws.owner(), null).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
        // 送审后出现在审核队列里。
        send(post("/v1/survey-templates/" + id + "/submit"), ws.owner(), null).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("pending"));
        send(get("/v1/survey-templates/pending"), reviewer, null).andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(id));

        send(post("/v1/survey-templates/" + id + "/approve"), reviewer, null).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("published"))
                .andExpect(jsonPath("$.publishedVersion").value(1));
        send(get("/v1/survey-templates?scope=tenant"), ws.owner(), null).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].name").value("标准满意度"));
        send(get("/v1/survey-templates/" + id), ws.owner(), null).andExpect(status().isOk())
                .andExpect(jsonPath("$.template.id").value(id))
                .andExpect(jsonPath("$.versions.length()").value(1))
                .andExpect(jsonPath("$.history[0].event").value("created"));

        String copy = send(post("/v1/survey-templates/" + id + "/copies"), ws.owner(),
                Map.of("parentId", ws.project().toString(), "title", "从模板新建"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("draft"))
                .andExpect(jsonPath("$.title").value("从模板新建"))
                .andReturn().getResponse().getContentAsString();
        assertThat(JsonPath.<String>read(copy, "$.id")).isNotEqualTo(id);

        send(post("/v1/survey-templates/" + id + "/unpublish"), reviewer, null).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("unpublished"));
        send(post("/v1/survey-templates/" + id + "/copies"), ws.owner(),
                Map.of("parentId", ws.project().toString(), "title", "下架后"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("template_not_published"));
    }

    @Test
    void anotherTenantGetsA404NotA403() throws Exception {
        String id = JsonPath.read(send(post("/v1/survey-templates"), ws.owner(),
                Map.of("sourceSurveyId", surveyId.toString(), "name", "标准满意度"))
                .andReturn().getResponse().getContentAsString(), "$.id");
        Workspace other = fixture.workspace();

        send(get("/v1/survey-templates/" + id), other.owner(), null).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
        send(post("/v1/survey-templates/" + id + "/submit"), other.owner(), null)
                .andExpect(status().isNotFound());
        send(get("/v1/survey-templates?scope=tenant"), other.owner(), null).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void aRejectionWithoutAReasonIsA400AndTheReasonComesBackOnTheDetail() throws Exception {
        String id = JsonPath.read(send(post("/v1/survey-templates"), ws.owner(),
                Map.of("sourceSurveyId", surveyId.toString(), "name", "待审"))
                .andReturn().getResponse().getContentAsString(), "$.id");
        send(post("/v1/survey-templates/" + id + "/submit"), ws.owner(), null).andExpect(status().isOk());

        send(post("/v1/survey-templates/" + id + "/reject"), reviewer, Map.of("reason", " "))
                .andExpect(status().isBadRequest());
        send(post("/v1/survey-templates/" + id + "/reject"), reviewer, Map.of("reason", "题目顺序要改"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("rejected"))
                .andExpect(jsonPath("$.reviewReason").value("题目顺序要改"));
    }

    @Test
    void aMemberWithoutTheReviewerRoleGetsA403OnApprove() throws Exception {
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", ws.project());
        String id = JsonPath.read(send(post("/v1/survey-templates"), editor,
                Map.of("sourceSurveyId", surveyId.toString(), "name", "编辑者的模板"))
                .andReturn().getResponse().getContentAsString(), "$.id");
        send(post("/v1/survey-templates/" + id + "/submit"), editor, null).andExpect(status().isOk());

        send(post("/v1/survey-templates/" + id + "/approve"), editor, null).andExpect(status().isForbidden());
    }

    // ------------------------------------------------------------ 运营模板

    @Test
    void anOperatorPublishesATemplateEveryTenantCanCopy() throws Exception {
        String created = mvc.perform(post("/v1/platform/survey-templates")
                        .header("Authorization", operatorBearer())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of(
                                "name", "行业通用问卷", "definition", fixture.definition()))))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.scope").value("platform"))
                .andExpect(jsonPath("$.status").value("draft"))
                .andReturn().getResponse().getContentAsString();
        String id = JsonPath.read(created, "$.id");

        // 未上架：租户看不见，也抄不走。（运营模板库是全局的，只能断言"不含这一条"。）
        assertThat(platformLibraryIds(ws.owner())).doesNotContain(id);
        send(post("/v1/survey-templates/" + id + "/copies"), ws.owner(),
                Map.of("parentId", ws.project().toString(), "title", "抄不走"))
                .andExpect(status().isNotFound());

        mvc.perform(post("/v1/platform/survey-templates/" + id + "/publish")
                        .header("Authorization", operatorBearer())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("versionNo", 1))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("published"));

        assertThat(platformLibraryIds(ws.owner())).contains(id);
        send(get("/v1/survey-templates/" + id), ws.owner(), null).andExpect(status().isOk())
                .andExpect(jsonPath("$.template.scope").value("platform"));
        String copy = send(post("/v1/survey-templates/" + id + "/copies"), ws.owner(),
                Map.of("parentId", ws.project().toString(), "title", "从运营模板新建"))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        assertThat(JsonPath.<String>read(copy, "$.title")).isEqualTo("从运营模板新建");

        // 下架：之后抄不走，已经抄出去的还在。
        mvc.perform(post("/v1/platform/survey-templates/" + id + "/unpublish")
                        .header("Authorization", operatorBearer()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.status").value("unpublished"));
        send(post("/v1/survey-templates/" + id + "/copies"), ws.owner(),
                Map.of("parentId", ws.project().toString(), "title", "下架后"))
                .andExpect(status().isNotFound());
        send(get("/v1/surveys/" + JsonPath.<String>read(copy, "$.id")), ws.owner(), null)
                .andExpect(status().isOk());
    }

    @Test
    void aTenantTokenCannotReachTheOperatorEndpoints() throws Exception {
        mvc.perform(get("/v1/platform/survey-templates")
                        .header("Authorization", bearer(ws.owner())))
                .andExpect(status().isForbidden());
        mvc.perform(post("/v1/platform/survey-templates")
                        .header("Authorization", bearer(ws.owner()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("name", "x", "definition", fixture.definition()))))
                .andExpect(status().isForbidden());
    }

    @Test
    void anOperatorTemplateCarriesNoProductionData() throws Exception {
        var production = fixture.definition();
        production.putArray("participants").addObject().put("token", "tok-operator-1");
        String id = JsonPath.read(mvc.perform(post("/v1/platform/survey-templates")
                        .header("Authorization", operatorBearer())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("name", "带参与者", "definition", production))))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString(), "$.id");

        mvc.perform(post("/v1/platform/survey-templates/" + id + "/publish")
                        .header("Authorization", operatorBearer())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(json.writeValueAsString(Map.of("versionNo", 1))))
                .andExpect(status().isOk());
        String definition = send(get("/v1/survey-templates/" + id + "/versions/1"), ws.owner(), null)
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(definition).doesNotContain("tok-operator-1").doesNotContain("participants");
    }

    // ------------------------------------------------------------ 零件

    private ResultActions send(MockHttpServletRequestBuilder request, TenantContext ctx, Object body)
            throws Exception {
        request.header("Authorization", bearer(ctx));
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body));
        }
        return mvc.perform(request);
    }

    /** 运营模板库对所有租户共用，并行的用例会往里放东西，所以只断言"含 / 不含这一条"。 */
    private List<String> platformLibraryIds(TenantContext ctx) throws Exception {
        String body = send(get("/v1/survey-templates?scope=platform"), ctx, null)
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$[*].id");
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private String operatorBearer() {
        return "Bearer " + tokens.issue("ops-1", UUID.randomUUID(), List.of(PlatformRoles.OPERATOR));
    }
}
