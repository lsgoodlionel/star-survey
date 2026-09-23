package cn.mjy.platform.survey;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
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

/** HTTP 层：旧版恢复只改草稿，版本列表与在线标记不变；乐观锁与不存在的版本各有明确错误。 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyVersionRestoreApiTest {

    private static final String SURVEYS = "/v1/surveys";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private TestTokens tokens;

    private Workspace ws;
    private UUID survey;

    @BeforeEach
    void twoPublishedVersions() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        publisher.publish(ws.owner(), survey);
        surveys.saveDraft(ws.owner(), survey, 1, fixture.definitionTitled("第二版"));
        publisher.publish(ws.owner(), survey);
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions call(MockHttpServletRequestBuilder request) throws Exception {
        return mvc.perform(request.header("Authorization", bearer(ws.owner())));
    }

    private ResultActions restore(int versionNo, String body) throws Exception {
        return call(post(SURVEYS + "/" + survey + "/versions/" + versionNo + "/restore")
                .contentType(MediaType.APPLICATION_JSON).content(body));
    }

    @Test
    void restoringOverHttpReturnsTheNewDraftAndLeavesTheLiveVersionAlone() throws Exception {
        restore(1, "{\"expectedVersion\":2}").andExpect(status().isOk())
                .andExpect(jsonPath("$.version").value(3))
                .andExpect(jsonPath("$.definition.title").value("P0-00.8 发布网关样例问卷"));

        call(get(SURVEYS + "/" + survey + "/versions")).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].live").value(false))
                .andExpect(jsonPath("$[1].live").value(true))
                .andExpect(jsonPath("$[1].definition.title").value("第二版"));
    }

    @Test
    void aStaleExpectedVersionIsAConflict() throws Exception {
        restore(1, "{\"expectedVersion\":1}").andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("version_conflict"));
    }

    @Test
    void restoringAVersionThatDoesNotExistIsNotFound() throws Exception {
        restore(9, "{\"expectedVersion\":2}").andExpect(status().isNotFound());
    }

    @Test
    void theExpectedVersionIsRequired() throws Exception {
        restore(1, "{}").andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
    }
}
