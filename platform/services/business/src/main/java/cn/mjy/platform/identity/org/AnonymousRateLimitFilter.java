package cn.mjy.platform.identity.org;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 匿名端点的限流：事件回调 {@code /v1/org-events/**} 与免登 {@code /v1/auth/org/**}。
 *
 * <p>判定在验签之前、读请求体之前完成，因此洪水不会耗掉解密与数据库查询。两层桶，都按来源地址分：
 * 「来源地址」一层（跨连接，挡住换连接号乱撒的扫描），「来源地址 × 连接」一层。
 * 因为每层都含来源地址，一个来源的噪声吃不掉另一个来源的额度——攻击者锁不死某个租户的真实回调。
 *
 * <p>事件回调另有一份「已验签」额度：下游返回 2xx（内容相关的拒绝一律是统一的 401，见
 * {@link OrgLoginErrorHandler}）说明签名对得上，此时把刚扣的两个令牌退回去，改记在该连接的已验签桶上。
 * 真实回调因此不消耗未验签额度，开放平台的正常流量再多也不会被同来源的噪声拖下水；
 * 已验签额度用尽后不再退还，正当流量回到普通额度，总量仍然有界。免登端点没有「验签」可言，不退还。
 *
 * <p>来源地址取直连对端（{@code getRemoteAddr}）。刻意不认 {@code X-Forwarded-For}：
 * 没有可信代理清单时它可被伪造，认了等于让攻击者随意换桶。部署在反向代理后面时须由代理或入口层
 * 提供真实地址，否则所有来源共用一个桶——那时退还机制仍保证验签通过的真实回调不受噪声影响。
 *
 * <p>429 的响应体不说明原因，和统一 401 一样不泄露连接是否存在、签名错在哪里。
 *
 * <p>刻意不注册为 Spring Bean：Bean 形式的 Filter 会被 Spring Boot 自动挂到整个 Servlet 容器上，
 * 这里只应挂在这两条安全过滤链里（同 {@code EngineEventSignatureFilter}）。
 */
final class AnonymousRateLimitFilter extends OncePerRequestFilter {

    static final String RATE_LIMITED = "rate_limited";
    static final String RATE_LIMITED_MESSAGE = "too many requests";
    /** 连接号不是 UUID（或路径形状不对）时共用的桶键：攻击者无法靠乱写连接号凭空生成桶。 */
    static final String UNKNOWN_CONNECTION = "-";

    private static final int UUID_LENGTH = 36;
    private static final Logger log = LoggerFactory.getLogger(AnonymousRateLimitFilter.class);

    private final String basePath;
    private final TokenBucketLimiter perSource;
    private final TokenBucketLimiter perConnection;
    /** 为 {@code null} 表示这条链没有「验签通过」可言（免登端点）。 */
    private final TokenBucketLimiter verified;
    private final int retryAfterSeconds;

    private AnonymousRateLimitFilter(String basePath, OrgRateLimitProperties limits, boolean hasVerifiedTraffic) {
        this.basePath = basePath;
        this.perSource = new TokenBucketLimiter(
                limits.perSourcePerMinute(), limits.maxBuckets(), System::nanoTime);
        this.perConnection = new TokenBucketLimiter(
                limits.perConnectionPerMinute(), limits.maxBuckets(), System::nanoTime);
        this.verified = hasVerifiedTraffic ? new TokenBucketLimiter(
                limits.verifiedPerConnectionPerMinute(), limits.maxBuckets(), System::nanoTime) : null;
        this.retryAfterSeconds = limits.retryAfterSeconds();
    }

    /** 事件回调：验签通过（2xx）的请求把令牌退回未验签的桶。 */
    static AnonymousRateLimitFilter forEvents(String basePath, OrgRateLimitProperties limits) {
        return new AnonymousRateLimitFilter(basePath, limits, true);
    }

    /** 免登：谁都能拿到 302，2xx/3xx 不代表来者正当，因此不退还。 */
    static AnonymousRateLimitFilter forLogin(String basePath, OrgRateLimitProperties limits) {
        return new AnonymousRateLimitFilter(basePath, limits, false);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String source = source(request);
        String connection = connectionOf(request);
        String pair = source + "|" + connection;
        // 来源额度照扣不退：换连接号乱撒的扫描同样要计入这个来源。
        if (!perSource.tryAcquire(source) || !perConnection.tryAcquire(pair)) {
            log.info("rate limited an anonymous request on {} from {}", basePath, source);
            reject(response);
            return;
        }
        chain.doFilter(request, response);
        if (verified != null && response.getStatus() < HttpStatus.BAD_REQUEST.value()
                && verified.tryAcquire(connection)) {
            perConnection.refund(pair);
            perSource.refund(source);
        }
    }

    private void reject(HttpServletResponse response) throws IOException {
        response.setStatus(HttpStatus.TOO_MANY_REQUESTS.value());
        response.setHeader(HttpHeaders.RETRY_AFTER, Integer.toString(retryAfterSeconds));
        response.setHeader(HttpHeaders.CACHE_CONTROL, "no-store");
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getOutputStream().write(("{\"error\":\"" + RATE_LIMITED + "\",\"message\":\""
                + RATE_LIMITED_MESSAGE + "\"}").getBytes(StandardCharsets.UTF_8));
    }

    private static String source(HttpServletRequest request) {
        String address = request.getRemoteAddr();
        return address == null ? "" : address;
    }

    /** 路径里的连接号；形状不对或不是 UUID 时归入同一个桶，键的数量因此不受调用方摆布。 */
    private String connectionOf(HttpServletRequest request) {
        String uri = request.getRequestURI();
        String context = request.getContextPath();
        if (context != null && !context.isEmpty() && uri.startsWith(context)) {
            uri = uri.substring(context.length());
        }
        if (!uri.startsWith(basePath)) {
            return UNKNOWN_CONNECTION;
        }
        String[] segments = uri.substring(basePath.length()).split("/");
        // segments[0] 是基路径之后的空串，[1] 是租户，[2] 是连接。
        return segments.length >= 3 && isUuid(segments[2])
                ? segments[2].toLowerCase(Locale.ROOT) : UNKNOWN_CONNECTION;
    }

    private static boolean isUuid(String value) {
        if (value.length() != UUID_LENGTH) {
            return false;
        }
        try {
            UUID.fromString(value);
            return true;
        } catch (IllegalArgumentException e) {
            return false;
        }
    }
}
