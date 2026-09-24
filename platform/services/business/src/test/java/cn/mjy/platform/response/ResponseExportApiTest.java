package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.response.ExportFixture.Member;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
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
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/** HTTP 层：202 + jobId、状态、再次授权的下载、取消、幂等键与请求校验。 */
@SpringBootTest
@AutoConfigureMockMvc
class ResponseExportApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private ResponseExportWorker worker;

    @Autowired
    private TestTokens tokens;

    private Published p;

    @BeforeEach
    void aPublishedSurveyWithOneResponse() {
        p = fixture.responses().publishedSurvey();
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);
        answers.put(p.instance(), p.sid(), 1, Map.of(PLAIN_FIELD, "A1"));
    }

    private MockHttpServletRequestBuilder as(TenantContext ctx, MockHttpServletRequestBuilder request) {
        return request.header("Authorization",
                "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of()));
    }

    private String exportsUrl() {
        return "/v1/surveys/" + p.surveyId() + "/exports";
    }

    private UUID createJob(TenantContext ctx, String body) throws Exception {
        MvcResult result = mvc.perform(as(ctx, post(exportsUrl()))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isAccepted())
                .andReturn();
        return UUID.fromString(JsonPath.read(result.getResponse().getContentAsString(), "$.jobId"));
    }

    @Test
    void creatingReturns202WithJobIdAndExpiryThenTheFileDownloadsWithNoStore() throws Exception {
        MvcResult created = mvc.perform(as(p.owner(), post(exportsUrl()))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"format\":\"xlsx\"}"))
                .andExpect(status().isAccepted())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.status").value("queued"))
                .andExpect(jsonPath("$.format").value("xlsx"))
                .andExpect(jsonPath("$.expiresAt").isNotEmpty())
                .andReturn();
        UUID job = UUID.fromString(JsonPath.read(created.getResponse().getContentAsString(), "$.jobId"));
        assertThat(created.getResponse().getHeader("Location")).isEqualTo("/v1/exports/" + job);

        mvc.perform(as(p.owner(), get("/v1/exports/" + job + "/download")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("export_not_ready"));
        worker.process(p.tenant(), job, Integer.MAX_VALUE);

        mvc.perform(as(p.owner(), get("/v1/exports/" + job)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("completed"))
                .andExpect(jsonPath("$.totalRows").value(1))
                .andExpect(jsonPath("$.processedRows").value(1))
                .andExpect(jsonPath("$.sha256").isNotEmpty());
        MvcResult file = mvc.perform(as(p.owner(), get("/v1/exports/" + job + "/download")))
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(header().string("Content-Type",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
                .andExpect(header().string("Content-Disposition",
                        "attachment; filename=\"survey-" + p.surveyId() + "-export-" + job + ".xlsx\""))
                .andReturn();
        byte[] bytes = file.getResponse().getContentAsByteArray();
        assertThat(bytes).startsWith('P', 'K');
    }

    @Test
    void theBlueprintAliasTakesTheSurveyInTheBody() throws Exception {
        mvc.perform(as(p.owner(), post("/v1/exports")).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"surveyId\":\"" + p.surveyId() + "\",\"format\":\"csv\","
                                + "\"filter\":{\"states\":[\"engine_completed\"]},\"templateVersion\":\"default\"}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.surveyId").value(p.surveyId().toString()))
                .andExpect(jsonPath("$.filter.states[0]").value("engine_completed"));
    }

    @Test
    void theSameIdempotencyKeyReturnsTheSameJobAndADifferentBodyIs409() throws Exception {
        String key = "export-" + UUID.randomUUID();
        MvcResult first = mvc.perform(as(p.owner(), post(exportsUrl())).header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"format\":\"csv\"}"))
                .andExpect(status().isAccepted()).andReturn();
        MvcResult again = mvc.perform(as(p.owner(), post(exportsUrl())).header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"format\":\"csv\"}"))
                .andExpect(status().isAccepted()).andReturn();

        assertThat(JsonPath.<String>read(again.getResponse().getContentAsString(), "$.jobId"))
                .isEqualTo(JsonPath.read(first.getResponse().getContentAsString(), "$.jobId"));
        mvc.perform(as(p.owner(), post(exportsUrl())).header("Idempotency-Key", key)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"format\":\"xlsx\"}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("idempotency_key_reused"));
    }

    @Test
    void invalidRequestsAre400() throws Exception {
        // pdf 还没实现；sav（切片 06.3）与 docx（切片 06.4）都已是合法取值，不能再拿它们当
        // "不认识的格式"的例子。
        for (String body : new String[] {"{\"format\":\"pdf\"}", "{\"format\":\"csv\",\"templateVersion\":\"v9\"}",
                "{\"format\":\"csv\",\"filter\":{\"states\":[\"bogus\"]}}", "{}", "not json"}) {
            mvc.perform(as(p.owner(), post(exportsUrl())).contentType(MediaType.APPLICATION_JSON).content(body))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error").value("invalid_request"));
        }
    }

    @Test
    void aStatisticsViewerIs403AndARevokedDownloaderIs403() throws Exception {
        Member stats = fixture.member(p, "stats", "statistics_viewer");
        mvc.perform(as(stats.ctx(), post(exportsUrl())).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"format\":\"csv\"}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"));

        Member raw = fixture.member(p, "raw", "raw_data_viewer");
        UUID job = createJob(raw.ctx(), "{\"format\":\"csv\"}");
        worker.process(p.tenant(), job, Integer.MAX_VALUE);
        fixture.revoke(p, raw);
        mvc.perform(as(raw.ctx(), get("/v1/exports/" + job + "/download")))
                .andExpect(status().isForbidden());
    }

    @Test
    void cancelAndUnknownJobs() throws Exception {
        UUID job = createJob(p.owner(), "{\"format\":\"csv\"}");

        mvc.perform(as(p.owner(), post("/v1/exports/" + job + "/cancel")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("cancelled"));
        mvc.perform(as(p.owner(), post("/v1/exports/" + job + "/cancel")))
                .andExpect(status().isConflict());
        mvc.perform(as(p.owner(), get("/v1/exports/" + UUID.randomUUID())))
                .andExpect(status().isNotFound());
        mvc.perform(as(p.owner(), get("/v1/exports/not-a-uuid")))
                .andExpect(status().isBadRequest());
    }

    @Test
    void anExpiredJobIs410() throws Exception {
        UUID job = createJob(p.owner(), "{\"format\":\"csv\"}");
        worker.process(p.tenant(), job, Integer.MAX_VALUE);
        fixture.expireJob(p.tenant(), job);
        worker.expireDue();

        mvc.perform(as(p.owner(), get("/v1/exports/" + job + "/download")))
                .andExpect(status().isGone())
                .andExpect(jsonPath("$.error").value("export_expired"));
    }
}
