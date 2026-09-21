package cn.mjy.platform.shared.security;

import java.nio.charset.StandardCharsets;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.web.SecurityFilterChain;

/**
 * 无状态 API：每个请求都必须携带已验签的 JWT，租户只从令牌里取。
 *
 * P1 用 HMAC 共享密钥验签，便于在没有身份服务的环境下开发与测试；
 * 接入 Keycloak 等身份服务后改为按发行方的 JWK 验签（见 ADR 0010）。
 */
@Configuration
public class SecurityConfig {

    private static final int MIN_SECRET_BYTES = 32;

    @Bean
    SecurityFilterChain api(HttpSecurity http) throws Exception {
        http
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health").permitAll()
                        .anyRequest().authenticated())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .oauth2ResourceServer(server -> server.jwt(jwt -> { }));
        return http.build();
    }

    @Bean
    JwtDecoder jwtDecoder(@Value("${platform.security.jwt-hmac-secret:}") String secret) {
        byte[] key = secret.getBytes(StandardCharsets.UTF_8);
        if (key.length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "platform.security.jwt-hmac-secret must be at least " + MIN_SECRET_BYTES + " bytes");
        }
        return NimbusJwtDecoder.withSecretKey(new SecretKeySpec(key, "HmacSHA256"))
                .macAlgorithm(MacAlgorithm.HS256)
                .build();
    }
}
