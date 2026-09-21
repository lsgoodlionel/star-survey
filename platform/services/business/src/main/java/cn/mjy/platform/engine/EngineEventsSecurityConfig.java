package cn.mjy.platform.engine;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.intercept.AuthorizationFilter;
import org.springframework.security.web.authentication.HttpStatusEntryPoint;

/**
 * 服务间接口 /internal/** 的独立安全过滤链，排在主链（用户 JWT，见 shared/security/SecurityConfig）之前。
 *
 * <ul>
 *   <li>只认引擎投递签名，不接受用户 JWT：用户令牌再高的权限也进不了内部接口；</li>
 *   <li>无会话、无 CSRF（没有浏览器与 Cookie 参与）；</li>
 *   <li>/internal/** 下以后新增的任何接口默认同样受签名保护。</li>
 * </ul>
 */
@Configuration
public class EngineEventsSecurityConfig {

    /** 数字越小越先匹配；主链没有标注顺序，处于最低优先级。 */
    static final int INTERNAL_CHAIN_ORDER = 1;

    @Bean
    @Order(INTERNAL_CHAIN_ORDER)
    SecurityFilterChain internalEngineEvents(HttpSecurity http, EventSignatureVerifier verifier) throws Exception {
        http
                .securityMatcher("/internal/**")
                .authorizeHttpRequests(auth -> auth.anyRequest().hasRole(EngineEventSignatureFilter.RELAY_ROLE))
                .addFilterBefore(new EngineEventSignatureFilter(verifier), AuthorizationFilter.class)
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .requestCache(cache -> cache.disable())
                .exceptionHandling(errors -> errors.authenticationEntryPoint(
                        new HttpStatusEntryPoint(HttpStatus.UNAUTHORIZED)));
        return http.build();
    }
}
