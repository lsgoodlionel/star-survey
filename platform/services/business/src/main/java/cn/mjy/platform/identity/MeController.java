package cn.mjy.platform.identity;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** 返回当前请求被识别成的身份与租户，便于客户端与排查确认上下文。 */
@RestController
public class MeController {

    private final CurrentTenant currentTenant;

    public MeController(CurrentTenant currentTenant) {
        this.currentTenant = currentTenant;
    }

    @GetMapping("/v1/me")
    public Me me() {
        TenantContext context = currentTenant.require();
        return new Me(context.tenantId().toString(), context.actorId(), context.roles());
    }

    public record Me(String tenantId, String actorId, List<String> roles) {
    }
}
