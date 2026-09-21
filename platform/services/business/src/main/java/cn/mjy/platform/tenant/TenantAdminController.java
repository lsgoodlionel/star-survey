package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.api.InvalidRequestException;
import cn.mjy.platform.tenant.api.NotFoundException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 运营端：租户开通与状态变更。整个路径段由 {@link PlatformWebConfig} 要求平台运营角色。 */
@RestController
@RequestMapping("/v1/platform/tenants")
public class TenantAdminController {

    private final TenantService tenants;
    private final PlatformOperatorGuard guard;

    public TenantAdminController(TenantService tenants, PlatformOperatorGuard guard) {
        this.tenants = tenants;
        this.guard = guard;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public TenantView create(@Valid @RequestBody CreateTenant request) {
        String actor = guard.requireOperator();
        return TenantView.of(tenants.create(request.code(), request.name().strip(), actor, newTraceId()));
    }

    @GetMapping("/{tenantId}")
    public TenantView get(@PathVariable UUID tenantId) {
        guard.requireOperator();
        TenantId id = new TenantId(tenantId);
        return tenants.find(id).map(TenantView::of)
                .orElseThrow(() -> new NotFoundException("tenant not found: " + id));
    }

    @PostMapping("/{tenantId}/status")
    public TenantView changeStatus(@PathVariable UUID tenantId, @Valid @RequestBody ChangeStatus request) {
        String actor = guard.requireOperator();
        TenantStatus target = InvalidRequestException.parse(request.status(), TenantStatus::fromCode);
        return TenantView.of(tenants.changeStatus(new TenantId(tenantId), target, actor, newTraceId()));
    }

    private static String newTraceId() {
        return UUID.randomUUID().toString();
    }

    /** 租户编码：小写字母开头，小写字母、数字与连字符，2–40 位（用于子域名等场景）。 */
    public record CreateTenant(
            @NotBlank @Pattern(regexp = "[a-z][a-z0-9-]{1,39}") String code,
            @NotBlank @Size(max = 200) String name) {
    }

    public record ChangeStatus(@NotBlank String status) {
    }

    public record TenantView(String id, String code, String name, String status, String createdAt) {

        static TenantView of(Tenant tenant) {
            return new TenantView(tenant.id().toString(), tenant.code(), tenant.name(),
                    tenant.status().code(), tenant.createdAt().toString());
        }
    }
}
