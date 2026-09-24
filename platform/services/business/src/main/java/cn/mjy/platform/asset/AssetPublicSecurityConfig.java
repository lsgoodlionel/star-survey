package cn.mjy.platform.asset;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

/**
 * 匿名取件端点 {@code /a/**} 的独立过滤链（ADR 0019 决定 4）。
 *
 * <p>作答者没有平台令牌——他往往连账号都没有。防护不靠过滤链，而靠端点自己：
 * 地址里的取件票是按租户密钥签的 HMAC，覆盖租户、资产、版本与过期时刻。
 *
 * <p>链上只放 GET：拿着票<b>写不了</b>任何东西。无会话、无 Cookie，因此关闭 CSRF。
 * 主链（shared/security/SecurityConfig）行为不变。
 */
@Configuration
class AssetPublicSecurityConfig {

    /** 排在投放公开链（4）之后、主链（最低优先级）之前。 */
    static final int ASSET_PUBLIC_CHAIN_ORDER = 5;

    @Bean
    @Order(ASSET_PUBLIC_CHAIN_ORDER)
    SecurityFilterChain assetPublic(HttpSecurity http) throws Exception {
        http
                .securityMatcher("/a/**")
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .requestCache(cache -> cache.disable())
                .anonymous(anonymous -> { });
        return http.build();
    }
}
