package cn.mjy.platform.delivery;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Supplier;
import org.junit.jupiter.api.Test;

class EmailSettingsServiceTest {

    @Test
    void leavesStaticFallbackAvailableOnlyWhenTenantHasNoSavedSetting() {
        TenantId tenant = new TenantId(UUID.fromString("10000000-0000-4000-8000-000000000002"));
        TenantContext actor = new TenantContext(tenant, "owner", List.of("tenant_owner"), "trace-fallback");
        AccessDecisionService access = mock(AccessDecisionService.class);
        TenantScope scope = mock(TenantScope.class);
        EmailSettingRepository settings = mock(EmailSettingRepository.class);
        EmailCredentialCipher cipher = mock(EmailCredentialCipher.class);
        DeliveryKeys keys = mock(DeliveryKeys.class);
        SmtpTransport transport = mock(SmtpTransport.class);
        when(access.canInTenant(actor, Permission.MANAGE_SETTINGS))
                .thenReturn(new Decision(true, DecisionReason.GRANTED, "test"));
        when(scope.call(eq(tenant), any())).thenAnswer(invocation ->
                ((Supplier<?>) invocation.getArgument(1)).get());
        EmailSettingsService service = new EmailSettingsService(access, scope, settings, cipher, keys, transport);

        when(settings.find(tenant)).thenReturn(Optional.empty());
        assertNull(service.resolve(tenant));

        when(settings.find(tenant)).thenReturn(Optional.of(new EmailSettingRepository.Row(
                "netease-163", "app-password", "sender@163.com", "sender@163.com",
                new byte[] { 1 }, new byte[12], "owner", Instant.parse("2026-10-30T00:00:00Z"))));
        assertThrows(DeliveryUnavailableException.class, () -> service.resolve(tenant));
    }

    @Test
    void sendsATestMessageWithTheSavedEncryptedConfiguration() {
        TenantId tenant = new TenantId(UUID.fromString("10000000-0000-4000-8000-000000000001"));
        TenantContext actor = new TenantContext(tenant, "owner", List.of("tenant_owner"), "trace-test-send");
        AccessDecisionService access = mock(AccessDecisionService.class);
        TenantScope scope = mock(TenantScope.class);
        EmailSettingRepository settings = mock(EmailSettingRepository.class);
        EmailCredentialCipher cipher = mock(EmailCredentialCipher.class);
        DeliveryKeys keys = mock(DeliveryKeys.class);
        SmtpTransport transport = mock(SmtpTransport.class);
        when(access.canInTenant(actor, Permission.MANAGE_SETTINGS))
                .thenReturn(new Decision(true, DecisionReason.GRANTED, "test"));
        when(keys.isConfigured()).thenReturn(true);
        when(scope.call(eq(tenant), any())).thenAnswer(invocation ->
                ((Supplier<?>) invocation.getArgument(1)).get());
        byte[] ciphertext = new byte[] { 1, 2, 3 };
        byte[] nonce = new byte[12];
        when(settings.find(tenant)).thenReturn(Optional.of(new EmailSettingRepository.Row(
                "netease-163", "app-password", "sender@163.com", "sender@163.com",
                ciphertext, nonce, "owner", Instant.parse("2026-10-30T00:00:00Z"))));
        when(cipher.open(tenant, ciphertext, nonce)).thenReturn("app-password-secret");
        when(transport.submit(any(), any(), any())).thenReturn("<accepted@163.com>");
        EmailSettingsService service = new EmailSettingsService(access, scope, settings, cipher, keys, transport);

        EmailSettingsService.TestSendResult result = service.sendTest(actor,
                new EmailSettingsService.TestSendCommand(
                        "recipient@example.com", "投递测试", "这是一封测试邮件。"));

        assertTrue(result.accepted());
        assertEquals("测试邮件已被 SMTP 服务器接受，请检查收件箱及垃圾邮件目录", result.message());
        verify(transport).submit(any(), any(DeliveryMessage.class), any());
    }
}
