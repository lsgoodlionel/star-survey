package cn.mjy.platform.engine;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.engine.EngineInstanceDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.PlatformOperatorGuard;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 运营端：签发某个引擎实例的事件投递密钥（部署该实例时配置为 {@code MJY_PLATFORM_EVENTS_SECRET}）。
 *
 * <ul>
 *   <li>只有平台运营可以调用：路径在 /v1/platform/** 下，由租户模块的拦截器统一把关，方法内再显式检查一次；
 *       租户令牌 403，无令牌 401；</li>
 *   <li>只为已登记且在用的实例签发（按目录判定），否则 404——同时也拿到审计要记在哪个租户名下；</li>
 *   <li>每次签发都写审计（谁、何时、哪个实例），密钥本身不进日志、不进审计；响应禁止缓存。</li>
 * </ul>
 * 密钥由 {@link EngineEventKeys} 从主密钥派生，与验签使用的是同一个值；重复调用得到同一密钥（不是轮换）。
 */
@RestController
public class EngineEventSecretController {

    static final String PATH = "/v1/platform/engine-instances/{instanceId}/event-secret";
    public static final String AUDIT_ACTION = "engine_instance.event_secret_issue";

    private static final Logger log = LoggerFactory.getLogger(EngineEventSecretController.class);

    private final EngineEventKeys keys;
    private final PlatformOperatorGuard guard;
    private final ObjectProvider<EngineInstanceDirectory> directory;
    private final TenantScope tenantScope;
    private final AuditLogRepository audit;

    public EngineEventSecretController(EngineEventKeys keys, PlatformOperatorGuard guard,
            ObjectProvider<EngineInstanceDirectory> directory, TenantScope tenantScope, AuditLogRepository audit) {
        this.keys = keys;
        this.guard = guard;
        this.directory = directory;
        this.tenantScope = tenantScope;
        this.audit = audit;
    }

    @PostMapping(path = PATH, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> issue(@PathVariable String instanceId) {
        String actor = guard.requireOperator();
        if (!keys.isConfigured()) {
            return error(HttpStatus.SERVICE_UNAVAILABLE, "not_configured");
        }
        EngineInstanceDirectory instances = directory.getIfAvailable();
        if (instances == null) {
            return error(HttpStatus.SERVICE_UNAVAILABLE, "temporarily_unavailable");
        }
        Optional<TenantId> owner = instances.tenantOf(instanceId);
        if (owner.isEmpty()) {
            return error(HttpStatus.NOT_FOUND, "unknown_engine_instance");
        }
        TenantId tenant = owner.get();
        String traceId = UUID.randomUUID().toString();
        tenantScope.run(tenant, () -> audit.record(tenant, actor, AUDIT_ACTION, "engine_instance/" + instanceId, traceId));
        log.info("operator {} issued the event secret of engine instance {} (trace {})", actor, instanceId, traceId);
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(new IssuedSecret(instanceId, keys.derivedSecret(instanceId)));
    }

    private static ResponseEntity<?> error(HttpStatus status, String code) {
        return ResponseEntity.status(status).body(Map.of("error", code));
    }

    /** 签发结果：{@code secret} 即引擎侧 MJY_PLATFORM_EVENTS_SECRET 的值。 */
    public record IssuedSecret(String engineInstanceId, String secret) {
    }
}
