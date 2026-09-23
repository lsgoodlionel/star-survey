package cn.mjy.platform.identity.org;

import static cn.mjy.platform.identity.org.OrgEventRequests.nowSeconds;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/** 关掉限流（{@code platform.identity.rate-limit.enabled=false}）时过滤器根本不挂上去：再小的限额也不生效。 */
class OrgRateLimitDisabledTest extends OrgLoginTestSupport {

    @DynamicPropertySource
    static void limiterOff(DynamicPropertyRegistry registry) {
        registry.add("platform.identity.rate-limit.enabled", () -> false);
        registry.add("platform.identity.rate-limit.per-connection-per-minute", () -> 1);
        registry.add("platform.identity.rate-limit.per-source-per-minute", () -> 1);
    }

    @Test
    void aBurstIsStillAnsweredWithTheUniform401() throws Exception {
        Org org = connect(newTenant(), "wecom", "jit_staff");
        subscribeEvents(org);

        for (int i = 0; i < 10; i++) {
            mvc.perform(OrgEventRequests.wecomPostSealed(eventPath(org), "not-the-token", "Zm9v",
                            Long.toString(nowSeconds()), org.corp()))
                    .andExpect(status().isUnauthorized());
        }
    }
}
