package cn.mjy.platform.identity;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.tenant.api.NotFoundException;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户端：外部身份绑定与查询。租户只取自已验签令牌。
 * P1 不接 Keycloak，令牌仍由 HMAC 验签（见 ADR 0010）；这里只维护三元组到主体的映射。
 */
@RestController
@RequestMapping("/v1/identity-bindings")
public class IdentityBindingController {

    private final IdentityBindingService bindings;
    private final CurrentTenant currentTenant;

    public IdentityBindingController(IdentityBindingService bindings, CurrentTenant currentTenant) {
        this.bindings = bindings;
        this.currentTenant = currentTenant;
    }

    @PostMapping
    public ResponseEntity<BindingView> bind(@Valid @RequestBody BindRequest request) {
        TenantContext context = currentTenant.require();
        ExternalIdentity identity = new ExternalIdentity(request.provider(), request.appId(), request.externalId());
        return bindings.bind(context.tenantId(), identity, context.actorId(), context.traceId())
                .toResponse(BindingView::of);
    }

    @GetMapping("/{principalId}")
    public BindingView get(@PathVariable UUID principalId) {
        return bindings.findByPrincipal(currentTenant.require().tenantId(), principalId)
                .map(BindingView::of)
                .orElseThrow(IdentityBindingController::notFound);
    }

    @GetMapping(params = {"provider", "appId", "externalId"})
    public BindingView lookup(
            @RequestParam @NotBlank @Pattern(regexp = ExternalIdentity.PROVIDER_REGEX) String provider,
            @RequestParam @NotBlank @Size(max = ExternalIdentity.MAX_APP_ID) String appId,
            @RequestParam @NotBlank @Size(max = ExternalIdentity.MAX_EXTERNAL_ID) String externalId) {
        ExternalIdentity identity = new ExternalIdentity(provider, appId, externalId);
        return bindings.findByIdentity(currentTenant.require().tenantId(), identity)
                .map(BindingView::of)
                .orElseThrow(IdentityBindingController::notFound);
    }

    private static NotFoundException notFound() {
        return new NotFoundException("identity binding not found");
    }

    public record BindRequest(
            @NotBlank @Pattern(regexp = ExternalIdentity.PROVIDER_REGEX) String provider,
            @NotBlank @Size(max = ExternalIdentity.MAX_APP_ID) String appId,
            @NotBlank @Size(max = ExternalIdentity.MAX_EXTERNAL_ID) String externalId) {
    }

    public record BindingView(String principalId, String tenantId, String provider, String appId, String externalId) {

        static BindingView of(IdentityBinding binding) {
            ExternalIdentity identity = binding.identity();
            return new BindingView(binding.principalId().toString(), binding.tenantId().toString(),
                    identity.provider(), identity.appId(), identity.externalId());
        }
    }
}
