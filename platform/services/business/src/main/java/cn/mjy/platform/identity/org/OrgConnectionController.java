package cn.mjy.platform.identity.org;

import cn.mjy.platform.identity.IdentityBinding;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户端：组织连接（企业微信 / 钉钉 / 飞书）管理、成员预授权与组织同步。租户只取自已验签令牌。
 * 请求里只接受密钥名（secretRef），不接受密钥值；响应同样只有密钥名。
 */
@RestController
@RequestMapping("/v1/org-connections")
public class OrgConnectionController {

    static final String ID_REGEX = "[A-Za-z0-9_.-]{1,128}";
    static final String SECRET_REF_REGEX = "[A-Z][A-Z0-9_]{0,63}";

    private final OrgConnectionService connections;
    private final OrgDirectorySyncService sync;
    private final OrgLoginService login;
    private final CurrentTenant currentTenant;

    OrgConnectionController(OrgConnectionService connections, OrgDirectorySyncService sync, OrgLoginService login,
            CurrentTenant currentTenant) {
        this.connections = connections;
        this.sync = sync;
        this.login = login;
        this.currentTenant = currentTenant;
    }

    @PostMapping
    ResponseEntity<ConnectionView> create(@Valid @RequestBody CreateRequest request) {
        TenantContext ctx = currentTenant.require();
        OrgConnection created = connections.create(ctx, new OrgConnectionService.CreateCommand(
                OrgProvider.fromCode(request.provider()).orElseThrow(), request.corpId(), request.appId(),
                request.secretRef(), policy(request.unknownUserPolicy(), UnknownUserPolicy.REQUIRE_PREAUTHORIZED)));
        return ResponseEntity.status(HttpStatus.CREATED).body(view(created));
    }

    @GetMapping
    List<ConnectionView> list() {
        return connections.list(currentTenant.require()).stream().map(this::view).toList();
    }

    @GetMapping("/{id}")
    ConnectionView get(@PathVariable UUID id) {
        return view(connections.get(currentTenant.require(), id));
    }

    @PutMapping("/{id}")
    ConnectionView update(@PathVariable UUID id, @Valid @RequestBody UpdateRequest request) {
        OrgConnectionService.UpdateCommand command = new OrgConnectionService.UpdateCommand(request.secretRef(),
                policy(request.unknownUserPolicy(), null), request.enabled());
        return view(connections.update(currentTenant.require(), id, command));
    }

    @PostMapping("/{id}/authorized-users")
    ResponseEntity<AuthorizedUserView> preauthorize(@PathVariable UUID id,
            @Valid @RequestBody AuthorizeUserRequest request) {
        return connections.preauthorize(currentTenant.require(), id, request.externalId())
                .toResponse(AuthorizedUserView::of);
    }

    @PostMapping("/{id}/sync")
    OrgDirectorySyncService.SyncResult sync(@PathVariable UUID id) {
        return sync.sync(currentTenant.require(), id);
    }

    private ConnectionView view(OrgConnection c) {
        return new ConnectionView(c.id().toString(), c.provider().code(), c.corpId(), c.appId(), c.secretRef(),
                c.unknownUserPolicy().code(), c.enabled(), login.redirectUri(c.tenantId(), c.id()));
    }

    private static UnknownUserPolicy policy(String code, UnknownUserPolicy fallback) {
        return code == null ? fallback : UnknownUserPolicy.fromCode(code).orElseThrow();
    }

    record CreateRequest(
            @NotBlank @Pattern(regexp = OrgProvider.CODE_REGEX) String provider,
            @NotBlank @Pattern(regexp = ID_REGEX) String corpId,
            @NotBlank @Pattern(regexp = ID_REGEX) String appId,
            @NotBlank @Pattern(regexp = SECRET_REF_REGEX) String secretRef,
            @Pattern(regexp = UnknownUserPolicy.CODE_REGEX) String unknownUserPolicy) {
    }

    record UpdateRequest(
            @Pattern(regexp = SECRET_REF_REGEX) String secretRef,
            @Pattern(regexp = UnknownUserPolicy.CODE_REGEX) String unknownUserPolicy,
            Boolean enabled) {
    }

    record AuthorizeUserRequest(@NotBlank @Size(max = 256) @Pattern(regexp = "[^/\\s]+") String externalId) {
    }

    /** redirectUri：须在开放平台登记的回调地址（未配置平台对外地址时为空）。 */
    record ConnectionView(String id, String provider, String corpId, String appId, String secretRef,
            String unknownUserPolicy, boolean enabled, String redirectUri) {
    }

    record AuthorizedUserView(String principalId, String externalId) {

        static AuthorizedUserView of(IdentityBinding binding) {
            return new AuthorizedUserView(binding.principalId().toString(), binding.identity().externalId());
        }
    }
}
