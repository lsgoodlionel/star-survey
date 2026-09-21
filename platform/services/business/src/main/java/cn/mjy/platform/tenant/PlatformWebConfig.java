package cn.mjy.platform.tenant;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * /v1/platform/** 下的所有端点统一要求平台运营角色。放在拦截器里集中把关，
 * 以后新增的运营端点即使忘了在方法里检查，也不会对租户用户开放。
 */
@Configuration
public class PlatformWebConfig implements WebMvcConfigurer {

    public static final String OPERATOR_PATHS = "/v1/platform/**";

    private final PlatformOperatorGuard guard;

    public PlatformWebConfig(PlatformOperatorGuard guard) {
        this.guard = guard;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new HandlerInterceptor() {
            @Override
            public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
                guard.requireOperator();
                return true;
            }
        }).addPathPatterns(OPERATOR_PATHS);
    }
}
