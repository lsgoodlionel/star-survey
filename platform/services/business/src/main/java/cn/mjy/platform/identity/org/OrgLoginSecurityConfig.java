package cn.mjy.platform.identity.org;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.intercept.AuthorizationFilter;

/**
 * 免登端点 /v1/auth/org/** 的独立过滤链：匿名可访问（用户此时还没有令牌），不认 Authorization 头。
 * 防护不靠过滤链而靠流程本身：state 一次性、限时、绑定租户与浏览器 Cookie（见 {@link OrgLoginService}）。
 * 两个端点都是 GET 且不改变已登录用户的状态，因此不需要 CSRF 令牌；登录 CSRF 由浏览器绑定 Cookie 防住。
 * 主链（shared/security/SecurityConfig）行为不变。
 */
@Configuration
@EnableConfigurationProperties({OrgLoginProperties.class, OrgRateLimitProperties.class})
class OrgLoginSecurityConfig {

    /** 排在引擎内部链（1）之后、主链（最低优先级）之前。 */
    static final int ORG_LOGIN_CHAIN_ORDER = 2;

    @Bean
    @Order(ORG_LOGIN_CHAIN_ORDER)
    SecurityFilterChain orgLogin(HttpSecurity http, OrgRateLimitProperties limits) throws Exception {
        http
                .securityMatcher(OrgLoginController.BASE_PATH + "/**")
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .requestCache(cache -> cache.disable())
                .anonymous(anonymous -> { });
        if (limits.enabled()) {
            // start / callback 都是匿名的：先限流，再走一次性 state 与 Cookie 绑定。
            http.addFilterBefore(
                    AnonymousRateLimitFilter.forLogin(OrgLoginController.BASE_PATH, limits),
                    AuthorizationFilter.class);
        }
        return http.build();
    }
}
