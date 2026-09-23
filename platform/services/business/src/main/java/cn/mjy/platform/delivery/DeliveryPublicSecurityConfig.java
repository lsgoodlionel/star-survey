package cn.mjy.platform.delivery;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

/**
 * 匿名投放端点 /d/** 的独立过滤链：短链跳转、退订、渠道商回执。
 *
 * <p>三者都没有平台令牌可用——扫码的人、点退订的人、渠道商的服务器都不是平台用户。
 * 防护不靠过滤链，而靠端点自己：短链码约 64 位熵且失效即 404；退订令牌按租户密钥签名；
 * 回执按 (租户, 渠道商) 的共享密钥验签并带时间窗。
 *
 * <p>无会话、无 Cookie，因此关闭 CSRF：退订的 POST 不依赖任何登录态，伪造请求也只能让某人退订自己，
 * 拿不到任何数据。主链（shared/security/SecurityConfig）行为不变。
 */
@Configuration
class DeliveryPublicSecurityConfig {

    /** 排在组织事件链（3）之后、主链（最低优先级）之前。 */
    static final int DELIVERY_PUBLIC_CHAIN_ORDER = 4;

    @Bean
    @Order(DELIVERY_PUBLIC_CHAIN_ORDER)
    SecurityFilterChain deliveryPublic(HttpSecurity http) throws Exception {
        http
                .securityMatcher("/d/**")
                .authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .requestCache(cache -> cache.disable())
                .anonymous(anonymous -> { });
        return http.build();
    }
}
