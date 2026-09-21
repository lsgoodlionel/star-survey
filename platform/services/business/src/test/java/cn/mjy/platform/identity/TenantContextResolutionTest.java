package cn.mjy.platform.identity;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.support.TestTokens;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 租户只能来自可信身份（已验签的令牌），客户端参数不能自行指定或提升租户。
 */
@SpringBootTest
@AutoConfigureMockMvc
class TenantContextResolutionTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TestTokens tokens;

    @Test
    void requestsWithoutATokenAreRejected() throws Exception {
        mvc.perform(get("/v1/me")).andExpect(status().isUnauthorized());
    }

    @Test
    void theTenantComesFromTheVerifiedToken() throws Exception {
        UUID tenant = UUID.randomUUID();

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + tokens.issue("user-1", tenant, List.of("editor"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.tenantId").value(tenant.toString()))
                .andExpect(jsonPath("$.actorId").value("user-1"))
                .andExpect(jsonPath("$.roles[0]").value("editor"));
    }

    @Test
    void aTenantHeaderCannotOverrideTheTokensTenant() throws Exception {
        UUID tenant = UUID.randomUUID();

        mvc.perform(get("/v1/me")
                        .header("Authorization", "Bearer " + tokens.issue("user-1", tenant, List.of()))
                        .header("X-Tenant-Id", UUID.randomUUID().toString()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.tenantId").value(tenant.toString()));
    }

    @Test
    void aTokenWithoutATenantClaimIsForbidden() throws Exception {
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + tokens.issueWithoutTenant("user-1")))
                .andExpect(status().isForbidden());
    }

    @Test
    void aTamperedTokenIsRejected() throws Exception {
        String token = tokens.issue("user-1", UUID.randomUUID(), List.of());
        String tampered = token.substring(0, token.length() - 4) + "AAAA";

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + tampered))
                .andExpect(status().isUnauthorized());
    }
}
