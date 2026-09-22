package cn.mjy.platform.survey;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
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
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** HTTP 层：重新发布沿用同一个发布接口；版本列表标出在线版本；漂移检查按版本发起与查询。 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyRepublishApiTest {

    private static final String SURVEYS = "/v1/surveys";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private Workspace ws;
    private UUID survey;

    @BeforeEach
    void aPublishedSurvey() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        publisher.publish(ws.owner(), survey);
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions call(org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder request)
            throws Exception {
        return mvc.perform(request.header("Authorization", bearer(ws.owner())));
    }

    @Test
    void republishOverHttpMarksOnlyTheNewVersionLive() throws Exception {
        ObjectNode body = json.createObjectNode();
        body.put("expectedVersion", 1);
        body.set("definition", fixture.definitionTitled("第二版"));
        call(put(SURVEYS + "/" + survey + "/draft").contentType(MediaType.APPLICATION_JSON)
                .content(json.writeValueAsString(body))).andExpect(status().isOk());

        call(post(SURVEYS + "/" + survey + "/publish")).andExpect(status().isOk())
                .andExpect(jsonPath("$.survey.publishedVersion").value(2))
                .andExpect(jsonPath("$.version.version").value(2))
                .andExpect(jsonPath("$.version.live").value(true));

        call(get(SURVEYS + "/" + survey + "/versions")).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].live").value(false))
                .andExpect(jsonPath("$[0].supersededAt").isNotEmpty())
                .andExpect(jsonPath("$[0].engineClosedAt").isNotEmpty())
                .andExpect(jsonPath("$[1].live").value(true));
    }

    @Test
    void driftChecksAreTriggeredAndListedPerVersion() throws Exception {
        call(post(SURVEYS + "/" + survey + "/versions/1/drift-checks")).andExpect(status().isOk())
                .andExpect(jsonPath("$.outcome").value("match"))
                .andExpect(jsonPath("$.version").value(1));

        call(get(SURVEYS + "/" + survey + "/versions/1/drift-checks")).andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].outcome").value("match"));
        call(post(SURVEYS + "/" + survey + "/versions/7/drift-checks")).andExpect(status().isNotFound());
    }
}
