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
 * 通讯录事件回调 /v1/org-events/** 的独立过滤链：开放平台不带平台令牌，端点匿名可达、不认 Authorization 头。
 * 认证由各开放平台的签名、时间戳与加密承担（验签失败 401 且无副作用），请求体有上限（见 {@link OrgEventProperties}）。
 * 服务器对服务器调用、无 Cookie，因此关闭 CSRF。主链行为不变。
 */
@Configuration
@EnableConfigurationProperties({OrgEventProperties.class, OrgSyncProperties.class, OrgRateLimitProperties.class})
class OrgEventSecurityConfig {

    /** 排在免登链（2）之后、主链（最低优先级）之前。 */
    static final int ORG_EVENT_CHAIN_ORDER = 3;

    @Bean
    @Order(ORG_EVENT_CHAIN_ORDER)
    SecurityFilterChain orgEvents(HttpSecurity http, OrgRateLimitProperties limits) throws Exception {
        http
                .securityMatcher(OrgEventService.EVENT_PATH + "/**")
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .requestCache(cache -> cache.disable())
                .anonymous(anonymous -> { });
        if (limits.enabled()) {
            // 限流排在最前：洪水在验签、解密与任何数据库查询之前就被挡掉。
            http.addFilterBefore(
                    AnonymousRateLimitFilter.forEvents(OrgEventService.EVENT_PATH, limits),
                    AuthorizationFilter.class);
        }
        return http.build();
    }
}
