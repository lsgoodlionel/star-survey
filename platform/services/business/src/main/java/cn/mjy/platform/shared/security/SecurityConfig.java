package cn.mjy.platform.shared.security;

import jakarta.servlet.DispatcherType;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtValidators;
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
                        // 容器把框架层错误（如 415）转发到 /error 时，原请求已在自己的过滤链上完成认证；
                        // 不放行这次内部转发，非 JWT 链（如 /internal/**）的真实错误会被掩盖成 401。
                        // 直接请求 /error 属于普通请求，仍需令牌。
                        .dispatcherTypeMatchers(DispatcherType.ERROR).permitAll()
                        .requestMatchers("/actuator/health").permitAll()
                        .anyRequest().authenticated())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable())
                .oauth2ResourceServer(server -> server.jwt(jwt -> { }));
        return http.build();
    }

    /**
     * 除签名与时效外，其他模块可以提供额外的令牌校验（如身份模块的会话撤销检查），任一不通过即 401。
     * 没有额外校验器时行为与原先完全一致。
     */
    @Bean
    JwtDecoder jwtDecoder(@Value("${platform.security.jwt-hmac-secret:}") String secret,
                          ObjectProvider<OAuth2TokenValidator<Jwt>> extraValidators) {
        byte[] key = secret.getBytes(StandardCharsets.UTF_8);
        if (key.length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "platform.security.jwt-hmac-secret must be at least " + MIN_SECRET_BYTES + " bytes");
        }
        NimbusJwtDecoder decoder = NimbusJwtDecoder.withSecretKey(new SecretKeySpec(key, "HmacSHA256"))
                .macAlgorithm(MacAlgorithm.HS256)
                .build();
        List<OAuth2TokenValidator<Jwt>> validators = new ArrayList<>();
        validators.add(JwtValidators.createDefault());
        extraValidators.orderedStream().forEach(validators::add);
        decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(validators));
        return decoder;
    }
}
