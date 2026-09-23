package cn.mjy.platform.contacts;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

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
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.json.JsonMapper;

/** 通讯录 HTTP 接口：跨租户一律 404，权限不足按原因码 403。 */
@SpringBootTest
@AutoConfigureMockMvc
class ContactApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private TenantContext ownerA;
    private TenantContext ownerB;

    @BeforeEach
    void seed() {
        ownerA = fixture.newTenant();
        ownerB = fixture.newTenant();
    }

    @Test
    void contactsCanBeCreatedListedAndUpdated() throws Exception {
        String list = idOf(call(ownerA, post("/v1/contact-lists"),
                Map.of("name", "受众 " + UUID.randomUUID(), "kind", "respondent", "dedupeKey", "email")));

        String contact = idOf(call(ownerA, post("/v1/contacts"),
                Map.of("listId", list, "kind", "respondent", "displayName", "甲", "email", "ann@example.com"))
                .andExpect(status().isCreated()));

        mvc.perform(get("/v1/contacts").param("listId", list).header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].id").value(contact));

        call(ownerA, patch("/v1/contacts/" + contact), Map.of("displayName", "甲改"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.displayName").value("甲改"));
    }

    @Test
    void anotherTenantsContactsAreInvisibleAndUntouchable() throws Exception {
        String listA = idOf(call(ownerA, post("/v1/contact-lists"),
                Map.of("name", "受众 " + UUID.randomUUID(), "kind", "respondent", "dedupeKey", "email")));
        String contactA = idOf(call(ownerA, post("/v1/contacts"),
                Map.of("listId", listA, "kind", "respondent", "displayName", "甲", "email", "ann@example.com")));

        mvc.perform(get("/v1/contacts/" + contactA).header("Authorization", bearer(ownerB)))
                .andExpect(status().isNotFound());
        mvc.perform(get("/v1/contacts").param("listId", listA).header("Authorization", bearer(ownerB)))
                .andExpect(status().isNotFound());
        call(ownerB, patch("/v1/contacts/" + contactA), Map.of("displayName", "劫持"))
                .andExpect(status().isNotFound());
        call(ownerB, post("/v1/contacts"),
                Map.of("listId", listA, "kind", "respondent", "displayName", "乙", "email", "bob@example.com"))
                .andExpect(status().isNotFound());
    }

    @Test
    void aMemberWithoutManageMembersIsForbidden() throws Exception {
        TenantContext plain = fixture.plainMember(ownerA, "plain");

        mvc.perform(get("/v1/contacts").header("Authorization", bearer(plain)))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("no_matching_grant"));
    }

    @Test
    void anImportReportsItsPerRowOutcomes() throws Exception {
        String list = idOf(call(ownerA, post("/v1/contact-lists"),
                Map.of("name", "受众 " + UUID.randomUUID(), "kind", "respondent", "dedupeKey", "email")));

        String job = JsonPath.read(call(ownerA, post("/v1/contact-lists/" + list + "/imports"),
                        Map.of("format", "csv", "content", "name,email\n甲,ann@example.com\n乙,oops\n"))
                .andExpect(status().isAccepted()).andReturn().getResponse().getContentAsString(), "$.jobId");

        mvc.perform(get("/v1/contact-imports/" + job).header("Authorization", bearer(ownerA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalRows").value(2));
        mvc.perform(get("/v1/contact-imports/" + job).header("Authorization", bearer(ownerB)))
                .andExpect(status().isNotFound());
    }

    @Test
    void invalidRequestsAreRejectedWithFourHundred() throws Exception {
        call(ownerA, post("/v1/contact-lists"),
                Map.of("name", "", "kind", "respondent", "dedupeKey", "email"))
                .andExpect(status().isBadRequest());
        call(ownerA, post("/v1/contact-lists"),
                Map.of("name", "名单", "kind", "nope", "dedupeKey", "email"))
                .andExpect(status().isBadRequest());
    }

    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private ResultActions call(TenantContext ctx, MockHttpServletRequestBuilder request, Map<String, String> body)
            throws Exception {
        return mvc.perform(request.header("Authorization", bearer(ctx))
                .contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body)));
    }

    private static String idOf(ResultActions result) throws Exception {
        return JsonPath.read(result.andReturn().getResponse().getContentAsString(), "$.id");
    }
}
