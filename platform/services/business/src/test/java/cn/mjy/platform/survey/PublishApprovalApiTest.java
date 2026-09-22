package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** 发布审批的 HTTP 层：R01-09 子账户经 API 也绕不过审批；租户与操作者只取自令牌。 */
@SpringBootTest
@AutoConfigureMockMvc
class PublishApprovalApiTest {

    private static final String SURVEYS = "/v1/surveys";
    private static final String APPROVALS = "/v1/publish-approvals";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private JsonMapper json;

    private Workspace ws;
    private UUID survey;
    private TenantContext editor;
    private TenantContext reviewer;

    @BeforeEach
    void setUp() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        fixture.requirePublishApproval(ws, true);
        editor = fixture.member(ws, "sub-editor", "editor", survey);
        reviewer = fixture.member(ws, "reviewer", "publish_reviewer", ws.project());
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions submit(TenantContext ctx, UUID id, int draftVersion) throws Exception {
        ObjectNode body = json.createObjectNode().put("draftVersion", draftVersion);
        return mvc.perform(post(SURVEYS + "/" + id + "/approval-requests").header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }

    private String submitted() throws Exception {
        String body = submit(editor, survey, 1).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.id");
    }

    private ResultActions decide(TenantContext ctx, String request, String action, String bodyJson)
            throws Exception {
        var builder = post(APPROVALS + "/" + request + "/" + action).header("Authorization", bearer(ctx));
        if (bodyJson != null) {
            builder = builder.contentType(MediaType.APPLICATION_JSON).content(bodyJson);
        }
        return mvc.perform(builder);
    }

    private ResultActions publish(TenantContext ctx) throws Exception {
        return mvc.perform(post(SURVEYS + "/" + survey + "/publish").header("Authorization", bearer(ctx)));
    }

    @Test
    void aSubAccountMustGoThroughApprovalToPublishOverTheApi() throws Exception {
        publish(editor).andExpect(status().isForbidden()).andExpect(jsonPath("$.error").value("approval_required"));

        String request = submitted();
        publish(editor).andExpect(status().isForbidden()).andExpect(jsonPath("$.error").value("approval_required"));

        mvc.perform(get(APPROVALS + "/pending").header("Authorization", bearer(reviewer)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[?(@.id == '" + request + "')].status").value("pending"));
        decide(reviewer, request, "approve", null).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("approved"))
                .andExpect(jsonPath("$.decidedBy").value("reviewer"));

        publish(editor).andExpect(status().isOk())
                .andExpect(jsonPath("$.survey.status").value("published"))
                .andExpect(jsonPath("$.version.draftVersion").value(1));
        mvc.perform(get(APPROVALS + "/" + request).header("Authorization", bearer(editor)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.request.status").value("published"))
                .andExpect(jsonPath("$.history.length()").value(4));
        mvc.perform(get(SURVEYS + "/" + survey + "/approval-requests").header("Authorization", bearer(editor)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void savingTheDraftOverTheApiVoidsTheApproval() throws Exception {
        String request = submitted();
        decide(reviewer, request, "approve", null).andExpect(status().isOk());
        ObjectNode body = json.createObjectNode();
        body.put("expectedVersion", 1);
        body.set("definition", fixture.definitionTitled("改稿"));
        mvc.perform(put(SURVEYS + "/" + survey + "/draft").header("Authorization", bearer(editor))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)))
                .andExpect(status().isOk());

        publish(editor).andExpect(status().isForbidden()).andExpect(jsonPath("$.error").value("approval_required"));
        assertThat(gateway.callsFor(survey)).isEmpty();
    }

    @Test
    void aRejectionNeedsAReason() throws Exception {
        String request = submitted();

        decide(reviewer, request, "reject", "{}").andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
        decide(reviewer, request, "reject", "{\"reason\":\"请补充说明\"}").andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("rejected"))
                .andExpect(jsonPath("$.reason").value("请补充说明"));
    }

    @Test
    void onlyTheApplicantMayWithdraw() throws Exception {
        String request = submitted();

        decide(reviewer, request, "withdraw", null).andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("not_applicant"));
        decide(editor, request, "withdraw", null).andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("withdrawn"));
    }

    @Test
    void anApproverWithoutApprovePublishGetsA403() throws Exception {
        String request = submitted();
        TenantContext colleague = fixture.member(ws, "colleague", "editor", survey);

        decide(colleague, request, "approve", null).andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"));
    }

    @Test
    void aSecondOpenRequestAndAStaleDraftVersionAre409() throws Exception {
        submitted();

        submit(editor, survey, 1).andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("approval_already_open"));
        submit(editor, survey, 7).andExpect(status().isConflict());
    }

    @Test
    void anotherTenantGets404OnEveryApprovalEndpoint() throws Exception {
        String request = submitted();
        Workspace other = fixture.workspace();

        decide(other.owner(), request, "approve", null).andExpect(status().isNotFound());
        decide(other.owner(), request, "reject", "{\"reason\":\"x\"}").andExpect(status().isNotFound());
        decide(other.owner(), request, "withdraw", null).andExpect(status().isNotFound());
        mvc.perform(get(APPROVALS + "/" + request).header("Authorization", bearer(other.owner())))
                .andExpect(status().isNotFound());
        submit(other.owner(), survey, 1).andExpect(status().isNotFound());
        mvc.perform(get(APPROVALS + "/pending").header("Authorization", bearer(other.owner())))
                .andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(0));
        mvc.perform(get(APPROVALS + "/" + request).header("Authorization", bearer(editor)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.request.status").value("pending"));
    }
}
