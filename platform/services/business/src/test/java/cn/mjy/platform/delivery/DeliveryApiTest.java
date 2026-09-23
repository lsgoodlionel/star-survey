package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/** HTTP 层：租户端接口的状态码与错误码，以及三个匿名端点的行为。 */
@DeliveryIntegrationTest
@AutoConfigureMockMvc
class DeliveryApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private UnsubscribeTokens unsubscribeTokens;

    @Autowired
    private FakeEmailProvider email;

    private Published p;

    @BeforeEach
    void publishedSurvey() {
        email.reset();
        p = fixture.publishedSurvey();
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions postAs(TenantContext ctx, String url, String body) throws Exception {
        return mvc.perform(post(url).header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(body));
    }

    private ResultActions getAs(TenantContext ctx, String url) throws Exception {
        return mvc.perform(get(url).header("Authorization", bearer(ctx)));
    }

    @Test
    void creatingAndReadingALinkThroughHttp() throws Exception {
        String created = postAs(p.owner(), "/v1/delivery/surveys/" + p.surveyId() + "/links", """
                {"label":"官网首页","params":{"src":"site"},"ttlSeconds":86400,"shortLink":true}
                """)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.label").value("官网首页"))
                .andExpect(jsonPath("$.shortUrl").isString())
                .andReturn().getResponse().getContentAsString();

        String linkId = com.jayway.jsonpath.JsonPath.read(created, "$.id");
        getAs(p.owner(), "/v1/delivery/links/" + linkId)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.url").isString());

        byte[] png = mvc.perform(get("/v1/delivery/links/" + linkId + "/qr")
                        .header("Authorization", bearer(p.owner())))
                .andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.IMAGE_PNG))
                .andReturn().getResponse().getContentAsByteArray();
        assertThat(png).startsWith((byte) 0x89, (byte) 'P', (byte) 'N', (byte) 'G');

        String svg = mvc.perform(get("/v1/delivery/links/" + linkId + "/qr?format=svg")
                        .header("Authorization", bearer(p.owner())))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(svg).startsWith("<?xml").contains("<svg");

        getAs(p.owner(), "/v1/delivery/links/" + linkId + "/embed")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.snippet").value(org.hamcrest.Matchers.containsString("sandbox=")));
    }

    @Test
    void aBadFormatIsFourHundredAndAnUnknownLinkIsFourOhFour() throws Exception {
        LinkView link = fixture.link(p, "渠道");

        mvc.perform(get("/v1/delivery/links/" + link.id() + "/qr?format=bmp")
                        .header("Authorization", bearer(p.owner())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));

        getAs(p.owner(), "/v1/delivery/links/" + UUID.randomUUID())
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
    }

    @Test
    void sendingNeedsEditOnTheSurvey() throws Exception {
        TenantContext viewer = fixture.responses().member(p, "api-viewer", "statistics_viewer");

        postAs(viewer, "/v1/delivery/tasks", """
                {"surveyId":"%s","channel":"email","subject":"请填写","bodyTemplate":"%s",
                 "recipients":[{"address":"a@example.com"}]}
                """.formatted(p.surveyId(), DeliveryFixture.EMAIL_BODY))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"));
    }

    @Test
    void anUnknownSurveyIsFourOhFourNotForbidden() throws Exception {
        postAs(p.owner(), "/v1/delivery/tasks", """
                {"surveyId":"%s","channel":"email","subject":"请填写","bodyTemplate":"%s",
                 "recipients":[{"address":"a@example.com"}]}
                """.formatted(UUID.randomUUID(), DeliveryFixture.EMAIL_BODY))
                .andExpect(status().isNotFound());
    }

    @Test
    void requestsWithoutATokenAreRejected() throws Exception {
        mvc.perform(get("/v1/delivery/links/" + UUID.randomUUID())).andExpect(status().isUnauthorized());
        mvc.perform(post("/v1/delivery/tasks").contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void theShortLinkEndpointRedirectsAnonymouslyAndFourOhFoursForUnknownCodes() throws Exception {
        LinkView link = fixture.link(p, "扫码进场");
        String code = link.shortUrl().substring(link.shortUrl().lastIndexOf('/') + 1);

        mvc.perform(get("/d/s/" + code))
                .andExpect(status().isFound())
                .andExpect(header().string("Location", link.url()))
                .andExpect(header().string("Cache-Control", "no-store"));

        mvc.perform(get("/d/s/" + ShortCodes.random())).andExpect(status().isNotFound());
        mvc.perform(get("/d/s/not-a-code")).andExpect(status().isNotFound());
    }

    /** 退订用 POST：邮件客户端预取 GET 不应该把人退掉。 */
    @Test
    void theUnsubscribePageIsReadOnlyAndThePostPerformsIt() throws Exception {
        TaskView task = fixture.emailTask(p, 1);
        fixture.run(p.tenant(), task.id());
        UUID recipientId = fixture.recipientIdOf(p.tenant(), task.id(), 1);
        String token = unsubscribeTokens.issue(p.tenant(), task.id(), recipientId);

        mvc.perform(get("/d/u/" + token))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_HTML));
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .singleElement()
                .satisfies(recipient -> assertThat(recipient.state()).isEqualTo("sent"));

        mvc.perform(post("/d/u/" + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.unsubscribed").value(true))
                .andExpect(jsonPath("$.alreadyUnsubscribed").value(false));

        mvc.perform(post("/d/u/" + token + "x")).andExpect(status().isNotFound());
    }

    @Test
    void theReceiptEndpointNeedsASignature() throws Exception {
        mvc.perform(post("/d/receipts/" + p.tenant().value() + "/fake-email")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"events\":[{\"eventId\":\"e1\",\"kind\":\"delivered\"}]}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("unauthorized"));
    }

    @Test
    void theReceiptSecretNeedsManageSettings() throws Exception {
        TenantContext viewer = fixture.responses().member(p, "secret-viewer", "statistics_viewer");

        getAs(viewer, "/v1/delivery/receipt-secret?provider=fake-email").andExpect(status().isForbidden());

        getAs(p.owner(), "/v1/delivery/receipt-secret?provider=fake-email")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.secret").isString())
                .andExpect(jsonPath("$.callbackUrl")
                        .value("https://survey.example/d/receipts/" + p.tenant().value() + "/fake-email"));
    }
}
