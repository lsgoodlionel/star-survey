package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 租户端：查看本租户的引擎实例（只读）。租户只取自已验签令牌。 */
@RestController
@RequestMapping("/v1/engine-instances")
public class EngineInstanceController {

    private final EngineInstanceService instances;
    private final CurrentTenant currentTenant;

    public EngineInstanceController(EngineInstanceService instances, CurrentTenant currentTenant) {
        this.instances = instances;
        this.currentTenant = currentTenant;
    }

    @GetMapping
    public List<EngineInstanceView> list() {
        TenantId tenant = currentTenant.require().tenantId();
        return instances.list(tenant).stream().map(EngineInstanceView::of).toList();
    }

    @GetMapping("/{instanceId}")
    public EngineInstanceView get(@PathVariable String instanceId) {
        TenantId tenant = currentTenant.require().tenantId();
        return instances.find(tenant, instanceId).map(EngineInstanceView::of)
                .orElseThrow(() -> EngineInstanceService.notFound(instanceId));
    }
}
