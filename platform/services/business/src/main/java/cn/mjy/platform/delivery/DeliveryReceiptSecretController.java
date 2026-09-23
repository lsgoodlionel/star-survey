package cn.mjy.platform.delivery;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.Map;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户管理员读取"某个渠道商的回执共享密钥"，以便配置到渠道商后台。
 *
 * <p>密钥从主密钥按 (租户, 渠道商) 派生，平台不另存一份；因此这里是取回它的唯一入口。
 * 需要租户级的 {@link Permission#MANAGE_SETTINGS}——能改租户设置的人才配拿到密钥。
 * 密钥<b>只在响应体里出现</b>，不写日志、不进审计（审计只记"谁在什么时候取过哪个渠道商的密钥"）。
 */
@RestController
@RequestMapping("/v1/delivery")
public class DeliveryReceiptSecretController {

    private static final Pattern PROVIDER = Pattern.compile("[a-z0-9][a-z0-9-]{0,63}");
    private static final Logger log = LoggerFactory.getLogger(DeliveryReceiptSecretController.class);

    private final AccessDecisionService access;
    private final DeliveryKeys keys;
    private final DeliveryAudit audit;
    private final DeliveryProperties properties;
    private final CurrentTenant currentTenant;
    private final TenantScope tenantScope;

    DeliveryReceiptSecretController(AccessDecisionService access, DeliveryKeys keys, DeliveryAudit audit,
            DeliveryProperties properties, CurrentTenant currentTenant, TenantScope tenantScope) {
        this.access = access;
        this.keys = keys;
        this.audit = audit;
        this.properties = properties;
        this.currentTenant = currentTenant;
        this.tenantScope = tenantScope;
    }

    @GetMapping("/receipt-secret")
    Map<String, String> receiptSecret(@RequestParam String provider) {
        TenantContext ctx = currentTenant.require();
        if (!PROVIDER.matcher(provider).matches()) {
            throw DeliveryExceptions.invalid("provider must be 1 to 64 lowercase letters, digits or hyphens");
        }
        if (!access.canInTenant(ctx, Permission.MANAGE_SETTINGS).allowed()) {
            throw new AccessDeniedException("manage-settings is required to read the receipt secret");
        }
        // 审计表也按租户隔离，写入必须在租户作用域里。
        tenantScope.run(ctx.tenantId(),
                () -> audit.record(ctx, DeliveryAudit.RECEIPT, "receipt-secret read provider=" + provider));
        log.info("receipt secret handed out for tenant {} provider {}", ctx.tenantId(), provider);
        return Map.of(
                "provider", provider,
                "secret", keys.receiptSecret(ctx.tenantId(), provider),
                "callbackUrl", properties.requirePublicBaseUrl() + ReceiptService.PATH
                        + ctx.tenantId() + "/" + provider,
                "signatureHeader", ReceiptService.SIGNATURE_HEADER,
                "timestampHeader", ReceiptService.TIMESTAMP_HEADER);
    }
}
