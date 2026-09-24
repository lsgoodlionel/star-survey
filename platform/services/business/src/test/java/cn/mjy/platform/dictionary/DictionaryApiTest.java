package cn.mjy.platform.dictionary;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.tenant.PlatformRoles;
import java.util.List;
import java.util.Map;
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
 * HTTP 层：运营端点必须带运营角色，租户端点只读已发布的版本。
 */
@SpringBootTest
@AutoConfigureMockMvc
class DictionaryApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AccessFixture access;

    @Autowired
    private DictionaryFixture fixture;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private JsonMapper json;

    private TenantContext ctx;
    private String code;

    @BeforeEach
    void aTenantAndAPublishedDictionary() {
        ctx = access.newTenant();
        code = fixture.newDictionaryCode();
        fixture.publishedDictionary(code, "2024.1");
    }

    private ResultActions call(MockHttpServletRequestBuilder request, String bearer, Object body) throws Exception {
        request.header("Authorization", "Bearer " + bearer);
        if (body != null) {
            request.contentType(MediaType.APPLICATION_JSON).content(json.writeValueAsString(body));
        }
        return mvc.perform(request);
    }

    private String tenantToken() {
        return tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }

    private String operatorToken() {
        return tokens.issue("ops", ctx.tenantId().value(), List.of(PlatformRoles.OPERATOR));
    }

    @Test
    void aTenantReadsTheChildrenOfALevel() throws Exception {
        call(get("/v1/dictionaries/" + code + "/versions/2024.1/nodes?size=20"), tenantToken(), null)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(2))
                .andExpect(jsonPath("$.nodes[0].code").value(DictionaryFixture.BEIJING))
                .andExpect(jsonPath("$.nodes[0].leaf").value(false));

        call(get("/v1/dictionaries/" + code + "/versions/2024.1/nodes?parent="
                + DictionaryFixture.BEIJING_CITY), tenantToken(), null)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nodes.length()").value(2));
    }

    @Test
    void aTenantSearchesAndResolvesAPath() throws Exception {
        call(get("/v1/dictionaries/" + code + "/versions/2024.1/search?q=罗湖"), tenantToken(), null)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].code").value(DictionaryFixture.LUOHU));

        call(get("/v1/dictionaries/" + code + "/versions/2024.1/nodes/" + DictionaryFixture.LUOHU
                + "/path"), tenantToken(), null)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(3))
                .andExpect(jsonPath("$[0].code").value(DictionaryFixture.GUANGDONG));
    }

    @Test
    void anUnpublishedVersionIsNotFound() throws Exception {
        call(get("/v1/dictionaries/" + code + "/versions/1999.1/nodes"), tenantToken(), null)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("not_found"));
    }

    @Test
    void anEmptyKeywordIsABadRequest() throws Exception {
        call(get("/v1/dictionaries/" + code + "/versions/2024.1/search?q="), tenantToken(), null)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
    }

    @Test
    void writingADictionaryNeedsTheOperatorRole() throws Exception {
        call(post("/v1/platform/dictionaries"), tenantToken(),
                Map.of("code", fixture.newDictionaryCode(), "name", "行政区划", "maxDepth", 3))
                .andExpect(status().isForbidden());
    }

    @Test
    void theOperatorWalksTheWholeLoopOverHttp() throws Exception {
        String fresh = fixture.newDictionaryCode();
        call(post("/v1/platform/dictionaries"), operatorToken(),
                Map.of("code", fresh, "name", "行政区划", "maxDepth", 2))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.currentVersion").doesNotExist());

        call(post("/v1/platform/dictionaries/" + fresh + "/versions"), operatorToken(),
                Map.of("version", "2024.1"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status").value("draft"));

        call(post("/v1/platform/dictionaries/" + fresh + "/versions/2024.1/nodes"), operatorToken(),
                Map.of("nodes", List.of(
                        Map.of("code", "110000", "label", "北京市"),
                        Map.of("code", "110100", "parentCode", "110000", "label", "市辖区"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nodeCount").value(2));

        call(post("/v1/platform/dictionaries/" + fresh + "/versions/2024.1/publish"), operatorToken(), null)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("published"))
                .andExpect(jsonPath("$.nodeCount").value(2));

        call(get("/v1/dictionaries/" + fresh + "/versions/2024.1/nodes"), tenantToken(), null)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nodes[0].label").value("北京市"));
    }

    @Test
    void anOrphanNodeIsRejectedWhenItIsLoadedOverHttp() throws Exception {
        String fresh = fixture.newDictionaryCode();
        call(post("/v1/platform/dictionaries"), operatorToken(),
                Map.of("code", fresh, "name", "行政区划", "maxDepth", 2)).andExpect(status().isCreated());
        call(post("/v1/platform/dictionaries/" + fresh + "/versions"), operatorToken(),
                Map.of("version", "2024.1")).andExpect(status().isCreated());

        call(post("/v1/platform/dictionaries/" + fresh + "/versions/2024.1/nodes"), operatorToken(),
                Map.of("nodes", List.of(Map.of("code", "110100", "parentCode", "999999", "label", "市辖区"))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
    }

    @Test
    void anEmptyVersionCannotBePublishedOverHttp() throws Exception {
        String fresh = fixture.newDictionaryCode();
        call(post("/v1/platform/dictionaries"), operatorToken(),
                Map.of("code", fresh, "name", "行政区划", "maxDepth", 2)).andExpect(status().isCreated());
        call(post("/v1/platform/dictionaries/" + fresh + "/versions"), operatorToken(),
                Map.of("version", "2024.1")).andExpect(status().isCreated());

        call(post("/v1/platform/dictionaries/" + fresh + "/versions/2024.1/publish"), operatorToken(), null)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_request"));
    }
}
