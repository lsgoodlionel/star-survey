package cn.mjy.platform.platformadmin;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.annotation.Order;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

/** This one endpoint validates its separate bearer credential inside its controller. */
@Configuration
class ProbeSetupSecurityConfig {
    @Bean @Order(6)
    SecurityFilterChain probeSetupChain(HttpSecurity http) throws Exception {
        return http.securityMatcher("/v1/setup-probe").authorizeHttpRequests(auth -> auth.anyRequest().permitAll())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .csrf(csrf -> csrf.disable()).build();
    }
}
