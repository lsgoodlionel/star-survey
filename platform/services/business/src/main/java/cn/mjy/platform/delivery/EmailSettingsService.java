package cn.mjy.platform.delivery;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import jakarta.mail.internet.AddressException;
import jakarta.mail.internet.InternetAddress;
import java.time.Instant;
import java.util.UUID;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;

/** 管理端 SMTP 设置：凭据仅在写入、解密发送及显式连通性检查的短暂内存中出现。 */
@Service
final class EmailSettingsService {
    private final AccessDecisionService access;
    private final TenantScope scope;
    private final EmailSettingRepository settings;
    private final EmailCredentialCipher cipher;
    private final DeliveryKeys keys;
    private final SmtpTransport transport;

    EmailSettingsService(AccessDecisionService access, TenantScope scope, EmailSettingRepository settings,
            EmailCredentialCipher cipher, DeliveryKeys keys, SmtpTransport transport) {
        this.access = access; this.scope = scope; this.settings = settings; this.cipher = cipher; this.keys = keys;
        this.transport = transport;
    }

    View view(TenantContext actor) {
        require(actor);
        return scope.call(actor.tenantId(), () -> settings.find(actor.tenantId())
                .map(row -> new View(true, row.provider(), row.authMode(), row.fromAddress(), row.username(), row.updatedAt()))
                .orElse(new View(false, null, null, null, null, null)));
    }

    View save(TenantContext actor, Command command) {
        require(actor);
        requireKeys();
        DeliveryEmailProperties resolved = properties(command.provider(), command.authMode(), command.fromAddress(),
                command.username(), command.credential());
        DeliveryEmailProperties.Resolved checked = resolved.resolve(java.time.Duration.ofMinutes(2));
        EmailCredentialCipher.Envelope envelope = cipher.seal(actor.tenantId(), command.credential());
        return scope.call(actor.tenantId(), () -> {
            settings.upsert(actor.tenantId(), checked.profile().code(), checked.authMode().code(), checked.fromAddress(),
                    checked.username(), envelope.ciphertext(), envelope.nonce(), actor.actorId());
            return view(actor);
        });
    }

    void clear(TenantContext actor) { require(actor); scope.run(actor.tenantId(), () -> settings.delete(actor.tenantId())); }

    VerifyResult verify(TenantContext actor, Command command) {
        require(actor);
        requireKeys();
        DeliveryEmailProperties.Resolved checked = properties(command.provider(), command.authMode(), command.fromAddress(),
                command.username(), command.credential()).resolve(java.time.Duration.ofMinutes(2));
        try {
            transport.verify(checked);
            return new VerifyResult(true, "SMTP 登录验证成功；未发送邮件");
        } catch (SmtpTransportException exception) {
            return new VerifyResult(false, switch (exception.kind()) {
                case AUTHENTICATION -> "SMTP 认证被拒绝，请核对授权码或 OAuth token";
                case TEMPORARY -> "SMTP 服务暂时不可用，请稍后重试";
                default -> "SMTP 连接或配置验证失败";
            });
        }
    }

    TestSendResult sendTest(TenantContext actor, TestSendCommand command) {
        require(actor);
        String recipient = requireRecipient(command.recipient());
        String subject = requireText(command.subject(), "email test subject", 200);
        String body = requireText(command.body(), "email test body", 10_000);
        DeliveryEmailProperties.Resolved checked = resolve(actor.tenantId());
        if (checked == null) {
            throw new DeliveryUnavailableException("email settings are not configured");
        }
        String idempotencyKey = "settings-test:" + UUID.randomUUID();
        String messageId = SmtpEmailDeliveryProvider.messageId(idempotencyKey, checked.messageIdDomain());
        try {
            transport.submit(checked,
                    new DeliveryMessage(actor.tenantId(), DeliveryChannel.EMAIL, recipient, subject, body,
                            idempotencyKey), messageId);
            return new TestSendResult(true, "测试邮件已被 SMTP 服务器接受，请检查收件箱及垃圾邮件目录");
        } catch (SmtpTransportException exception) {
            return new TestSendResult(false, switch (exception.kind()) {
                case AUTHENTICATION -> "SMTP 认证被拒绝，请重新保存有效授权信息";
                case RECIPIENT_REJECTED -> "测试收件人被 SMTP 服务器拒绝，请核对邮箱地址";
                case TEMPORARY -> "SMTP 服务暂时不可用，请稍后重试";
                default -> "测试邮件发送失败，请检查邮件配置";
            });
        }
    }

    DeliveryEmailProperties.Resolved resolve(cn.mjy.platform.shared.TenantId tenant) {
        return scope.call(tenant, () -> settings.find(tenant).map(row -> {
            // A deployment-wide static SMTP fallback remains usable when this tenant has
            // no saved setting. Once a tenant setting exists, however, a missing master
            // key must fail closed instead of silently sending from the fallback account.
            requireKeys();
            String credential = cipher.open(tenant, row.ciphertext(), row.nonce());
            return properties(row.provider(), row.authMode(), row.fromAddress(), row.username(), credential)
                    .resolve(java.time.Duration.ofMinutes(2));
        }).orElse(null));
    }

    boolean isConfigured(cn.mjy.platform.shared.TenantId tenant) {
        return keys.isConfigured() && scope.call(tenant, () -> settings.find(tenant).isPresent());
    }

    private void require(TenantContext actor) {
        if (!access.canInTenant(actor, Permission.MANAGE_SETTINGS).allowed()) throw new AccessDeniedException("manage-settings is required");
    }

    private void requireKeys() {
        if (!keys.isConfigured()) throw new DeliveryUnavailableException(DeliveryKeys.MASTER_SECRET_PROPERTY + " is not configured");
    }

    private static DeliveryEmailProperties properties(String provider, String authMode, String fromAddress,
            String username, String credential) {
        return new DeliveryEmailProperties(true, provider, authMode, fromAddress, username, credential,
                java.time.Duration.ofSeconds(10), java.time.Duration.ofSeconds(30), java.time.Duration.ofSeconds(30));
    }

    private static String requireRecipient(String value) {
        String recipient = value == null ? "" : value.trim();
        try {
            InternetAddress address = new InternetAddress(recipient, true);
            address.validate();
            return recipient;
        } catch (AddressException exception) {
            throw DeliveryExceptions.invalid("test recipient must be a valid email address");
        }
    }

    private static String requireText(String value, String name, int maxLength) {
        String text = value == null ? "" : value.trim();
        if (text.isEmpty() || text.length() > maxLength) {
            throw DeliveryExceptions.invalid(name + " must contain between 1 and " + maxLength + " characters");
        }
        return text;
    }

    record Command(String provider, String authMode, String fromAddress, String username, String credential) { }
    record View(boolean configured, String provider, String authMode, String fromAddress, String username, Instant updatedAt) { }
    record VerifyResult(boolean verified, String message) { }
    record TestSendCommand(String recipient, String subject, String body) { }
    record TestSendResult(boolean accepted, String message) { }
}
