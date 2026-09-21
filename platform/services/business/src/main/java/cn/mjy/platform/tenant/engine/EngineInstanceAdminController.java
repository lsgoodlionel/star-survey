package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.PlatformOperatorGuard;
import cn.mjy.platform.tenant.api.InvalidRequestException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 运营端：为租户登记引擎实例、调整实例状态。 */
@RestController
@RequestMapping("/v1/platform/tenants/{tenantId}/engine-instances")
public class EngineInstanceAdminController {

    private final EngineInstanceService instances;
    private final PlatformOperatorGuard guard;

    public EngineInstanceAdminController(EngineInstanceService instances, PlatformOperatorGuard guard) {
        this.instances = instances;
        this.guard = guard;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public EngineInstanceView register(@PathVariable UUID tenantId, @Valid @RequestBody RegisterInstance request) {
        String actor = guard.requireOperator();
        return EngineInstanceView.of(instances.register(
                new TenantId(tenantId), request.id(), request.baseUrl(), actor, UUID.randomUUID().toString()));
    }

    @GetMapping
    public List<EngineInstanceView> list(@PathVariable UUID tenantId) {
        guard.requireOperator();
        return instances.list(new TenantId(tenantId)).stream().map(EngineInstanceView::of).toList();
    }

    @PostMapping("/{instanceId}/status")
    public EngineInstanceView changeStatus(@PathVariable UUID tenantId, @PathVariable String instanceId,
                                           @Valid @RequestBody ChangeStatus request) {
        String actor = guard.requireOperator();
        EngineInstanceStatus target = InvalidRequestException.parse(request.status(), EngineInstanceStatus::fromCode);
        return EngineInstanceView.of(instances.changeStatus(
                new TenantId(tenantId), instanceId, target, actor, UUID.randomUUID().toString()));
    }

    public record RegisterInstance(
            @NotBlank @Pattern(regexp = EngineInstance.ID_REGEX) String id,
            @NotBlank @Size(max = 2048) @Pattern(regexp = EngineInstance.BASE_URL_REGEX) String baseUrl) {
    }

    public record ChangeStatus(@NotBlank String status) {
    }
}
