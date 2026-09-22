package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/** HTTP 层：明细、摘要、字段字典三个端点的状态码与错误码。 */
@SpringBootTest
@AutoConfigureMockMvc
class ResponseQueryApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private ResponseFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private TestTokens tokens;

    private Published p;

    @BeforeEach
    void aPublishedSurveyWithOneResponse() {
        p = fixture.publishedSurvey();
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);
        answers.put(p.instance(), p.sid(), 1, Map.of(PLAIN_FIELD, "满意", SENSITIVE_FIELD, "13812345678"));
    }

    private ResultActions getAs(TenantContext ctx, String url) throws Exception {
        String bearer = "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
        return mvc.perform(get(url).header("Authorization", bearer));
    }

    private String responsesUrl() {
        return "/v1/surveys/" + p.surveyId() + "/responses";
    }

    @Test
    void theOwnerListsResponsesWithAnswersAndVersionTags() throws Exception {
        getAs(p.owner(), responsesUrl() + "?limit=10&state=engine_completed")
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].version").value(1))
                .andExpect(jsonPath("$.items[0].engineSid").value(p.sid()))
                .andExpect(jsonPath("$.items[0].responseId").value(1))
                .andExpect(jsonPath("$.items[0].state").value("engine_completed"))
                .andExpect(jsonPath("$.items[0].answersStatus").value("available"))
                .andExpect(jsonPath("$.items[0].answers." + SENSITIVE_FIELD).value("13812345678"))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());
    }

    @Test
    void theResponsesAliasTakesTheSurveyAsAQueryParameter() throws Exception {
        getAs(p.owner(), "/v1/responses?surveyId=" + p.surveyId())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1));
    }

    @Test
    void aManagerSeesTheSensitiveColumnMasked() throws Exception {
        TenantContext manager = fixture.member(p, "pm", "project_manager");

        getAs(manager, responsesUrl())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.sensitiveRevealed").value(false))
                .andExpect(jsonPath("$.items[0].answers." + PLAIN_FIELD).value("满意"))
                .andExpect(jsonPath("$.items[0].answers." + SENSITIVE_FIELD).value(ResponseFieldPolicy.MASK));
    }

    @Test
    void aStatisticsViewerIs403OnRowsButCanReadTheSummaryAndTheFields() throws Exception {
        TenantContext stats = fixture.member(p, "stats", "statistics_viewer");

        getAs(stats, responsesUrl())
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"))
                .andExpect(jsonPath("$.items").doesNotExist());
        getAs(stats, responsesUrl() + "/summary")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total.engineCompleted").value(1));
        getAs(stats, "/v1/surveys/" + p.surveyId() + "/fields")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.versions[0].fields[1].fieldname").value(SENSITIVE_FIELD))
                .andExpect(jsonPath("$.versions[0].fields[1].sensitive").value(true));
    }

    @Test
    void aProjectIdIsNotASurveyAndIs404OnEveryEndpoint() throws Exception {
        String project = p.ws().project().toString();

        getAs(p.owner(), "/v1/surveys/" + project + "/responses").andExpect(status().isNotFound());
        getAs(p.owner(), "/v1/surveys/" + project + "/responses/summary").andExpect(status().isNotFound());
        getAs(p.owner(), "/v1/surveys/" + project + "/fields").andExpect(status().isNotFound());
        getAs(p.owner(), "/v1/surveys/" + project + "/versions").andExpect(status().isNotFound());
    }

    @Test
    void anotherTenantsSurveyIs404OnEveryEndpoint() throws Exception {
        Published other = fixture.publishedSurvey();

        getAs(other.owner(), responsesUrl()).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
        getAs(other.owner(), responsesUrl() + "/summary").andExpect(status().isNotFound());
        getAs(other.owner(), "/v1/surveys/" + p.surveyId() + "/fields").andExpect(status().isNotFound());
        getAs(other.owner(), "/v1/responses?surveyId=" + p.surveyId()).andExpect(status().isNotFound());
    }

    @Test
    void badParametersAre400() throws Exception {
        getAs(p.owner(), responsesUrl() + "?state=done").andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
        getAs(p.owner(), responsesUrl() + "?cursor=bm90LWEtY3Vyc29y").andExpect(status().isBadRequest());
        getAs(p.owner(), responsesUrl() + "?limit=abc").andExpect(status().isBadRequest());
        getAs(p.owner(), responsesUrl() + "?limit=100000").andExpect(status().isBadRequest());
        getAs(p.owner(), "/v1/responses").andExpect(status().isBadRequest());
        getAs(p.owner(), "/v1/surveys/not-a-uuid/responses").andExpect(status().isBadRequest());
    }

    @Test
    void aCursorFromThePreviousPageContinuesTheListing() throws Exception {
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 2, COMPLETED);
        String first = getAs(p.owner(), responsesUrl() + "?limit=1").andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        String cursor = JsonPath.read(first, "$.nextCursor");

        getAs(p.owner(), responsesUrl() + "?limit=1&cursor=" + cursor)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].responseId").value(2))
                .andExpect(jsonPath("$.items[0].answersStatus").value("missing"));
    }

    @Test
    void anUnreachableGatewayIs503() throws Exception {
        answers.failFor(p.instance());

        getAs(p.owner(), responsesUrl()).andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("answers_unavailable"));
    }

    @Test
    void withoutATokenTheEndpointsAre401() throws Exception {
        mvc.perform(get(responsesUrl())).andExpect(status().isUnauthorized());
    }
}
