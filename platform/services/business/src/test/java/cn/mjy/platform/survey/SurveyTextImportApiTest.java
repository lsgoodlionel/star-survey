package cn.mjy.platform.survey;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
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
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** HTTP 层：批量文本导入先预览后确认；坏行与不可导入的题目都有明确的错误体。 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyTextImportApiTest {

    private static final String SURVEYS = "/v1/surveys";
    private static final String TEXT = """
            1. 您的性别？[单选]
            A. 男
            B. 女
            这一行认不出来
            2. 单选却没有选项[单选]
            """;

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private Workspace ws;
    private UUID survey;

    @BeforeEach
    void aDraftSurvey() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions call(MockHttpServletRequestBuilder request) throws Exception {
        return mvc.perform(request.header("Authorization", bearer(ws.owner())));
    }

    private ResultActions postJson(String path, ObjectNode body) throws Exception {
        return call(post(SURVEYS + "/" + survey + path).contentType(MediaType.APPLICATION_JSON)
                .content(json.writeValueAsString(body)));
    }

    @Test
    void previewShowsEveryQuestionItsTypeAndTheLinesItCouldNotRead() throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("text", TEXT);

        postJson("/import/preview", body).andExpect(status().isOk())
                .andExpect(jsonPath("$.questions.length()").value(2))
                .andExpect(jsonPath("$.questions[0].code").value("Q1"))
                .andExpect(jsonPath("$.questions[0].type").value("L"))
                .andExpect(jsonPath("$.questions[0].typeName").value("单选"))
                .andExpect(jsonPath("$.questions[0].importable").value(true))
                .andExpect(jsonPath("$.questions[0].options.length()").value(2))
                .andExpect(jsonPath("$.questions[1].importable").value(false))
                .andExpect(jsonPath("$.questions[1].problems[0].code").value("too_few_options"))
                .andExpect(jsonPath("$.problems.length()").value(1))
                .andExpect(jsonPath("$.problems[0].line").value(4))
                .andExpect(jsonPath("$.problems[0].code").value("unrecognized_line"));
    }

    @Test
    void confirmingWritesOnlyTheAcceptedQuestions() throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("expectedVersion", 1);
        body.put("text", TEXT);
        body.putArray("accept").add(0);

        postJson("/import", body).andExpect(status().isOk())
                .andExpect(jsonPath("$.version").value(2))
                .andExpect(jsonPath("$.definition.groups.length()").value(3))
                .andExpect(jsonPath("$.definition.groups[2].questions[0].code").value("Q1"));
    }

    @Test
    void acceptingAQuestionThePreviewRefusedIsUnprocessable() throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("expectedVersion", 1);
        body.put("text", TEXT);
        body.putArray("accept").add(1);

        postJson("/import", body).andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.error").value("invalid_import"))
                .andExpect(jsonPath("$.problems.length()").value(1));
    }

    @Test
    void anEmptyRequestIsABadRequest() throws Exception {
        postJson("/import/preview", json.createObjectNode()).andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));

        ObjectNode noAccept = json.createObjectNode();
        noAccept.put("expectedVersion", 1);
        noAccept.put("text", TEXT);
        noAccept.putArray("accept");
        postJson("/import", noAccept).andExpect(status().isBadRequest());
    }
}
