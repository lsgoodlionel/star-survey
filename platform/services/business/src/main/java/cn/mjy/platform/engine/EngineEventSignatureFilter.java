package cn.mjy.platform.engine;

import cn.mjy.platform.engine.EventSignatureVerifier.Verdict;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContext;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.context.SecurityContextHolderStrategy;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * /internal/** 的认证过滤器：先取 {@code X-Mjy-Engine-Instance} 声明的实例，读出原始请求体（有上限），
 * 用该实例的派生密钥按 HMAC 验签；通过后以"引擎中继"身份放行，把同一份字节交给下游解析，
 * 并把已认证的实例标识放进请求属性 {@link #AUTHENTICATED_INSTANCE_ATTRIBUTE}——下游只认这个属性，
 * 不再读请求头，也不信请求体里自称的实例。
 *
 * <p>刻意不注册为 Spring Bean：Spring Boot 会把 Bean 形式的 Filter 自动挂到整个 Servlet 容器上，
 * 这里只应挂在 /internal/** 的安全过滤链里（见 {@link EngineEventsSecurityConfig}）。
 */
final class EngineEventSignatureFilter extends OncePerRequestFilter {

    /** 一批 100 个事件约 35KB；1MiB 足够，同时防止未认证的大请求耗尽内存。 */
    static final int MAX_BODY_BYTES = 1024 * 1024;
    static final String RELAY_PRINCIPAL = "engine-relay";
    static final String RELAY_ROLE = "ENGINE_RELAY";
    /** 验签确认的引擎实例标识（String）。 */
    public static final String AUTHENTICATED_INSTANCE_ATTRIBUTE =
            "cn.mjy.platform.engine.EngineEventSignatureFilter.authenticatedInstance";

    private static final Logger log = LoggerFactory.getLogger(EngineEventSignatureFilter.class);

    private final EventSignatureVerifier verifier;
    private final SecurityContextHolderStrategy contexts = SecurityContextHolder.getContextHolderStrategy();

    EngineEventSignatureFilter(EventSignatureVerifier verifier) {
        this.verifier = verifier;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (request.getContentLengthLong() > MAX_BODY_BYTES) {
            reject(response, HttpStatus.PAYLOAD_TOO_LARGE, "payload_too_large");
            return;
        }
        String instance = request.getHeader(EventSignatureVerifier.INSTANCE_HEADER);
        byte[] body = readBounded(request.getInputStream());
        if (body.length > MAX_BODY_BYTES) {
            reject(response, HttpStatus.PAYLOAD_TOO_LARGE, "payload_too_large");
            return;
        }
        Verdict verdict = verifier.verify(
                instance,
                request.getHeader(EventSignatureVerifier.TIMESTAMP_HEADER),
                request.getHeader(EventSignatureVerifier.SIGNATURE_HEADER),
                body);
        if (verdict != Verdict.VALID) {
            log.warn("rejected engine event delivery from {}: {}", request.getRemoteAddr(), verdict);
            rejectFor(response, verdict);
            return;
        }
        authenticateAsRelay(instance);
        request.setAttribute(AUTHENTICATED_INSTANCE_ATTRIBUTE, instance);
        chain.doFilter(new CachedBodyRequest(request, body), response);
    }

    private void authenticateAsRelay(String instance) {
        SecurityContext context = contexts.createEmptyContext();
        context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(
                RELAY_PRINCIPAL + ":" + instance, null, List.of(new SimpleGrantedAuthority("ROLE_" + RELAY_ROLE))));
        contexts.setContext(context);
    }

    /** 最多读 MAX_BODY_BYTES + 1 字节：多读的那一个字节用来判断是否超限。 */
    private static byte[] readBounded(InputStream input) throws IOException {
        return input.readNBytes(MAX_BODY_BYTES + 1);
    }

    private static void rejectFor(HttpServletResponse response, Verdict verdict) throws IOException {
        switch (verdict) {
            case NOT_CONFIGURED -> reject(response, HttpStatus.SERVICE_UNAVAILABLE, "not_configured");
            case MISSING_ENGINE_INSTANCE -> reject(response, HttpStatus.UNAUTHORIZED, "missing_engine_instance");
            case MALFORMED_ENGINE_INSTANCE -> reject(response, HttpStatus.UNAUTHORIZED, "invalid_engine_instance");
            case MISSING_HEADERS -> reject(response, HttpStatus.UNAUTHORIZED, "missing_signature");
            case MALFORMED_TIMESTAMP -> reject(response, HttpStatus.UNAUTHORIZED, "invalid_timestamp");
            case TIMESTAMP_OUT_OF_WINDOW -> reject(response, HttpStatus.UNAUTHORIZED, "timestamp_out_of_window");
            default -> reject(response, HttpStatus.UNAUTHORIZED, "invalid_signature");
        }
    }

    private static void reject(HttpServletResponse response, HttpStatus status, String code) throws IOException {
        response.setStatus(status.value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getOutputStream().write(("{\"error\":\"" + code + "\"}").getBytes(StandardCharsets.UTF_8));
    }
}
