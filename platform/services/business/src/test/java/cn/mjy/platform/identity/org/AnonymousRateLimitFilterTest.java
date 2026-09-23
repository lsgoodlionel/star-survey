package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.ServletException;
import java.io.IOException;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * 过滤器的判定：429 的形状、来源与连接的分桶、验签通过（2xx）后的退还，以及关掉限流。
 * 真实签名与真实链路见 {@link OrgAnonymousRateLimitTest}。
 */
class AnonymousRateLimitFilterTest {

    private static final String BASE = "/v1/org-events";
    private static final UUID TENANT = UUID.randomUUID();
    private static final UUID CONNECTION = UUID.randomUUID();

    private static OrgRateLimitProperties limits(int perConnection, int perSource, int verified) {
        return new OrgRateLimitProperties(true, perConnection, perSource, verified, 1000, 30);
    }

    /** @param status 下游要返回的状态码 */
    private static MockHttpServletResponse run(AnonymousRateLimitFilter filter, String ip, UUID connection,
            int status) throws ServletException, IOException {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", BASE + "/" + TENANT + "/" + connection);
        request.setRemoteAddr(ip);
        MockHttpServletResponse response = new MockHttpServletResponse();
        filter.doFilter(request, response, new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res)
                    throws IOException, ServletException {
                ((MockHttpServletResponse) res).setStatus(status);
                super.doFilter(req, res);
            }
        });
        return response;
    }

    @Test
    void theBurstIsRejectedWith429RetryAfterAndABodyThatExplainsNothing() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forEvents(BASE, limits(2, 10, 10));

        assertThat(run(filter, "10.0.0.1", CONNECTION, 401).getStatus()).isEqualTo(401);
        assertThat(run(filter, "10.0.0.1", CONNECTION, 401).getStatus()).isEqualTo(401);

        MockHttpServletResponse rejected = run(filter, "10.0.0.1", CONNECTION, 401);
        assertThat(rejected.getStatus()).isEqualTo(429);
        assertThat(rejected.getHeader("Retry-After")).isEqualTo("30");
        assertThat(rejected.getHeader("Cache-Control")).isEqualTo("no-store");
        assertThat(rejected.getContentAsString())
                .isEqualTo("{\"error\":\"rate_limited\",\"message\":\"too many requests\"}")
                .doesNotContain(CONNECTION.toString());
    }

    @Test
    void oneSourceCannotDrainAnother() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forEvents(BASE, limits(1, 10, 10));

        assertThat(run(filter, "10.0.0.1", CONNECTION, 401).getStatus()).isEqualTo(401);
        assertThat(run(filter, "10.0.0.1", CONNECTION, 401).getStatus()).isEqualTo(429);

        assertThat(run(filter, "10.0.0.2", CONNECTION, 401).getStatus()).isEqualTo(401);
    }

    @Test
    void aSourceSprayingConnectionIdsStillRunsOutOfItsOwnAllowance() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forEvents(BASE, limits(100, 3, 10));

        for (int i = 0; i < 3; i++) {
            assertThat(run(filter, "10.0.0.3", UUID.randomUUID(), 401).getStatus()).isEqualTo(401);
        }

        assertThat(run(filter, "10.0.0.3", UUID.randomUUID(), 401).getStatus()).isEqualTo(429);
    }

    @Test
    void verifiedCallbacksAreRefundedSoTheyNeverRunOutOfTheUnverifiedAllowance() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forEvents(BASE, limits(2, 2, 50));

        for (int i = 0; i < 20; i++) {
            assertThat(run(filter, "10.0.0.4", CONNECTION, 200).getStatus()).as("call %d", i).isEqualTo(200);
        }
    }

    @Test
    void onceTheVerifiedAllowanceIsSpentGenuineTrafficFallsBackToTheOrdinaryBudget() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forEvents(BASE, limits(2, 10, 3));

        for (int i = 0; i < 5; i++) {
            run(filter, "10.0.0.5", CONNECTION, 200);
        }

        assertThat(run(filter, "10.0.0.5", CONNECTION, 200).getStatus()).isEqualTo(429);
    }

    @Test
    void theLoginChainNeverRefundsBecauseA302ProvesNothing() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forLogin(BASE, limits(2, 10, 10));

        assertThat(run(filter, "10.0.0.6", CONNECTION, 302).getStatus()).isEqualTo(302);
        assertThat(run(filter, "10.0.0.6", CONNECTION, 302).getStatus()).isEqualTo(302);

        assertThat(run(filter, "10.0.0.6", CONNECTION, 302).getStatus()).isEqualTo(429);
    }

    @Test
    void garbageConnectionSegmentsShareOneBucketRatherThanMintingNewOnes() throws Exception {
        AnonymousRateLimitFilter filter = AnonymousRateLimitFilter.forEvents(BASE, limits(2, 100, 10));

        assertThat(rawPath(filter, "10.0.0.7", "/v1/org-events/x/not-a-uuid").getStatus()).isEqualTo(401);
        assertThat(rawPath(filter, "10.0.0.7", "/v1/org-events/y/also-not-a-uuid").getStatus()).isEqualTo(401);

        assertThat(rawPath(filter, "10.0.0.7", "/v1/org-events/z/third-one").getStatus()).isEqualTo(429);
    }

    private static MockHttpServletResponse rawPath(AnonymousRateLimitFilter filter, String ip, String path)
            throws ServletException, IOException {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", path);
        request.setRemoteAddr(ip);
        MockHttpServletResponse response = new MockHttpServletResponse();
        filter.doFilter(request, response, new MockFilterChain() {
            @Override
            public void doFilter(jakarta.servlet.ServletRequest req, jakarta.servlet.ServletResponse res)
                    throws IOException, ServletException {
                ((MockHttpServletResponse) res).setStatus(401);
                super.doFilter(req, res);
            }
        });
        return response;
    }
}
