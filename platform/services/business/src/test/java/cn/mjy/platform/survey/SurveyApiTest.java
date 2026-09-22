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

/** HTTP 层：状态码与错误码要让调用方一眼看懂（尤其是"需要审核"的 403 与版本冲突的 409）。 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyApiTest {

    private static final String SURVEYS = "/v1/surveys";

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

    @BeforeEach
    void setUp() {
        ws = fixture.workspace();
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions createSurvey(TenantContext ctx) throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("parentId", ws.project().toString());
        body.set("definition", fixture.definition());
        return mvc.perform(post(SURVEYS).header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }

    private String createdSurveyId() throws Exception {
        String body = createSurvey(ws.owner()).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.id");
    }

    private ResultActions saveDraft(TenantContext ctx, String id, int expectedVersion) throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("expectedVersion", expectedVersion);
        body.set("definition", fixture.definition());
        return mvc.perform(put(SURVEYS + "/" + id + "/draft").header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }

    private ResultActions publish(TenantContext ctx, String id) throws Exception {
        return mvc.perform(post(SURVEYS + "/" + id + "/publish").header("Authorization", bearer(ctx)));
    }

    @Test
    void createReadSaveAndPublishOverHttp() throws Exception {
        String id = createdSurveyId();

        mvc.perform(get(SURVEYS + "/" + id).header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("draft"))
                .andExpect(jsonPath("$.draftVersion").value(1));
        mvc.perform(get(SURVEYS + "/" + id + "/draft").header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.definition.uuid").value(id));
        saveDraft(ws.owner(), id, 1).andExpect(status().isOk()).andExpect(jsonPath("$.version").value(2));

        publish(ws.owner(), id).andExpect(status().isOk())
                .andExpect(jsonPath("$.survey.status").value("published"))
                .andExpect(jsonPath("$.version.version").value(1))
                .andExpect(jsonPath("$.version.draftVersion").value(2));

        mvc.perform(get(SURVEYS + "/" + id + "/versions").header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].engineInstanceId").value(ws.engineInstanceId()));
        mvc.perform(get(SURVEYS + "/" + id + "/versions/1").header("Authorization", bearer(ws.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.fields.length()").value(5))
                .andExpect(jsonPath("$.fields[0].code").value("QSINGLE"));
    }

    @Test
    void aStaleDraftSaveIs409() throws Exception {
        String id = createdSurveyId();
        saveDraft(ws.owner(), id, 1).andExpect(status().isOk());

        saveDraft(ws.owner(), id, 1).andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("version_conflict"));
    }

    @Test
    void anInvalidDefinitionIs422() throws Exception {
        String id = createdSurveyId();
        ObjectNode body = json.createObjectNode();
        body.put("expectedVersion", 1);
        body.set("definition", json.createObjectNode().put("definitionVersion", 1));

        mvc.perform(put(SURVEYS + "/" + id + "/draft").header("Authorization", bearer(ws.owner()))
                        .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("invalid_definition"));
    }

    @Test
    void aSubAccountWithoutApprovalGetsA403ThatSaysApprovalIsRequired() throws Exception {
        String id = createdSurveyId();
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", UUID.fromString(id));
        fixture.requirePublishApproval(ws, true);

        publish(editor, id).andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("approval_required"));
        assertThat(gateway.callsFor(UUID.fromString(id))).isEmpty();
    }

    @Test
    void aStatisticsViewerGetsA403OnPublish() throws Exception {
        String id = createdSurveyId();
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", UUID.fromString(id));

        publish(viewer, id).andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"));
    }

    @Test
    void anotherTenantGets404ForReadsAndPublish() throws Exception {
        String id = createdSurveyId();
        Workspace other = fixture.workspace();

        mvc.perform(get(SURVEYS + "/" + id).header("Authorization", bearer(other.owner())))
                .andExpect(status().isNotFound());
        publish(other.owner(), id).andExpect(status().isNotFound());
        saveDraft(other.owner(), id, 1).andExpect(status().isNotFound());
        assertThat(gateway.callsFor(UUID.fromString(id))).isEmpty();
    }

    @Test
    void failedAndPendingPublishesMapToDistinctStatusCodes() throws Exception {
        String rejected = createdSurveyId();
        gateway.script(UUID.fromString(rejected), r -> FakePublishGateway.rejected("E_X"));
        publish(ws.owner(), rejected).andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.survey.status").value("publish_failed"))
                .andExpect(jsonPath("$.survey.lastPublish.failures[0]").value("E_X"));

        String broken = createdSurveyId();
        gateway.script(UUID.fromString(broken), r -> FakePublishGateway.engineFailed(77, "ROLLBACK FAILED"));
        publish(ws.owner(), broken).andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.survey.lastPublish.orphanEngineSid").value(77));

        String pending = createdSurveyId();
        gateway.timeoutAfterPublishing(UUID.fromString(pending));
        publish(ws.owner(), pending).andExpect(status().isAccepted())
                .andExpect(jsonPath("$.survey.status").value("pending_reconciliation"));
    }

    @Test
    void aSecondPublishOfAPublishedSurveyIs409() throws Exception {
        String id = createdSurveyId();
        publish(ws.owner(), id).andExpect(status().isOk());

        publish(ws.owner(), id).andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("already_published"));
    }

    @Test
    void creatingUnderAProjectWithoutEditRightsIs403() throws Exception {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", ws.project());

        createSurvey(viewer).andExpect(status().isForbidden());
    }
}
