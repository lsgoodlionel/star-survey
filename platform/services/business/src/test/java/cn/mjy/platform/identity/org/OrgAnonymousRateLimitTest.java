package cn.mjy.platform.identity.org;

import static cn.mjy.platform.identity.org.OrgEventRequests.nowSeconds;
import static cn.mjy.platform.identity.org.OrgEventRequests.wecomVerify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.request.RequestPostProcessor;

/**
 * 匿名端点（事件回调与免登 start）的限流：未验签的洪水被 429 挡住，而验签通过的真实回调不受影响。
 * 限额在本测试里调得很小，因此这个类有自己的 Spring 上下文。
 */
class OrgAnonymousRateLimitTest extends OrgLoginTestSupport {

    private static final int PER_CONNECTION = 4;
    private static final int PER_SOURCE = 6;
    /** 桶随上下文长存，每个用例因此用自己的来源地址，互不干扰。 */
    private static final AtomicInteger NEXT_SOURCE = new AtomicInteger();

    private final String noiseIp = "203.0.113." + NEXT_SOURCE.incrementAndGet();
    private final String providerIp = "198.51.100." + NEXT_SOURCE.incrementAndGet();

    @DynamicPropertySource
    static void tightLimits(DynamicPropertyRegistry registry) {
        registry.add("platform.identity.rate-limit.per-connection-per-minute", () -> PER_CONNECTION);
        registry.add("platform.identity.rate-limit.per-source-per-minute", () -> PER_SOURCE);
    }

    private static RequestPostProcessor from(String ip) {
        return request -> {
            request.setRemoteAddr(ip);
            return request;
        };
    }

    /** 签名对不上的回调：内容无关的拒绝，本该是统一的 401。 */
    private MockHttpServletRequestBuilder noise(Org org) {
        return OrgEventRequests.wecomPostSealed(eventPath(org), "not-the-token", "Zm9v",
                Long.toString(nowSeconds()), org.corp());
    }

    private MockHttpServletRequestBuilder signed(Org org, EventSecrets secrets) {
        return wecomVerify(eventPath(org), secrets, org.corp(), "1616140317555161061", nowSeconds());
    }

    @Test
    void aBurstOfUnverifiedEventsGets429WithRetryAfterAndNoReason() throws Exception {
        Org org = connect(newTenant(), "wecom", "jit_staff");
        subscribeEvents(org);

        for (int i = 0; i < PER_CONNECTION; i++) {
            mvc.perform(noise(org).with(from(noiseIp))).andExpect(status().isUnauthorized());
        }

        mvc.perform(noise(org).with(from(noiseIp)))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().exists("Retry-After"))
                .andExpect(jsonPath("$.error").value("rate_limited"))
                .andExpect(jsonPath("$.message").value("too many requests"));
    }

    @Test
    void signedEventsKeepPassingWhileNoiseFromAnotherSourceIsBeingRejected() throws Exception {
        Org org = connect(newTenant(), "wecom", "jit_staff");
        EventSecrets secrets = subscribeEvents(org);

        for (int i = 0; i < PER_SOURCE + 2; i++) {
            mvc.perform(noise(org).with(from(noiseIp)));
        }
        mvc.perform(noise(org).with(from(noiseIp))).andExpect(status().isTooManyRequests());

        // 真实回调来自开放平台的地址，验签通过：额度不被噪声吃掉，条数远超未验签限额也照常处理。
        for (int i = 0; i < PER_CONNECTION * 3; i++) {
            mvc.perform(signed(org, secrets).with(from(providerIp)))
                    .andExpect(status().isOk())
                    .andExpect(content().string("1616140317555161061"));
        }
    }

    @Test
    void noiseAgainstOneConnectionDoesNotLockOutAnother() throws Exception {
        Org noisy = connect(newTenant(), "wecom", "jit_staff");
        Org quiet = connect(newTenant(), "wecom", "jit_staff");
        subscribeEvents(noisy);
        EventSecrets secrets = subscribeEvents(quiet);

        for (int i = 0; i < PER_SOURCE + 2; i++) {
            mvc.perform(noise(noisy).with(from(noiseIp)));
        }
        mvc.perform(noise(noisy).with(from(noiseIp))).andExpect(status().isTooManyRequests());

        mvc.perform(signed(quiet, secrets).with(from(providerIp))).andExpect(status().isOk());
    }

    @Test
    void theAnonymousLoginStartEndpointIsRateLimitedToo() throws Exception {
        Org org = connect(newTenant(), "wecom", "jit_staff");
        String path = startPath(org.tenant(), org.connectionId());

        for (int i = 0; i < PER_CONNECTION; i++) {
            mvc.perform(get(path).with(from(noiseIp))).andExpect(status().isFound());
        }

        mvc.perform(get(path).with(from(noiseIp)))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().exists("Retry-After"));
    }

    @Test
    void anUnknownConnectionIsThrottledTheSameWayAsAKnownOne() throws Exception {
        String path = "/v1/org-events/" + UUID.randomUUID() + "/" + UUID.randomUUID();

        for (int i = 0; i < PER_CONNECTION; i++) {
            mvc.perform(OrgEventRequests.wecomPostSealed(path, "t", "Zm9v", Long.toString(nowSeconds()), "corp")
                    .with(from(noiseIp)));
        }

        mvc.perform(OrgEventRequests.wecomPostSealed(path, "t", "Zm9v", Long.toString(nowSeconds()), "corp")
                        .with(from(noiseIp)))
                .andExpect(status().isTooManyRequests());
    }
}
