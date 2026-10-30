package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import static org.mockito.Mockito.mock;
import cn.mjy.platform.shared.TenantId;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

/** 配置回归：租户可用性及静态配置回退。 */
class DeliveryEmailConfigurationTest {

    private final ApplicationContextRunner context = new ApplicationContextRunner()
            .withUserConfiguration(DeliveryConfig.class, DeliveryEmailConfig.class)
            .withBean(EmailSettingsService.class, () -> mock(EmailSettingsService.class));

    @Test
    void remainsFailClosedWhenEmailIsNotExplicitlyEnabled() {
        context.run(application -> {
            assertThat(application).hasNotFailed();
            assertThat(application.getBeansOfType(DeliveryProvider.class).values()).singleElement()
                    .satisfies(provider -> assertThat(provider.available(TenantId.random())).isFalse());
        });
    }

    @Test
    void registersExactlyOneEmailProviderAndNeverAnSmsProvider() {
        context.withPropertyValues(
                "platform.delivery.email.enabled=true",
                "platform.delivery.email.provider=gmail",
                "platform.delivery.email.auth-mode=app-password",
                "platform.delivery.email.from-address=sender@gmail.com",
                "platform.delivery.email.username=sender@gmail.com",
                "platform.delivery.email.credential=test-only-app-password")
                .run(application -> {
                    assertThat(application).hasNotFailed();
                    assertThat(application.getBeansOfType(DeliveryProvider.class).values())
                            .singleElement()
                            .satisfies(provider -> {
                                assertThat(provider.channel()).isEqualTo(DeliveryChannel.EMAIL);
                                assertThat(provider.name()).isEqualTo("smtp-configured");
                                assertThat(provider.available(TenantId.random())).isTrue();
                            });
                });
    }

    @Test
    void rejectsAnUnsupportedAuthenticationCombinationAtStartup() {
        context.withPropertyValues(
                "platform.delivery.email.enabled=true",
                "platform.delivery.email.provider=outlook-com",
                "platform.delivery.email.auth-mode=app-password",
                "platform.delivery.email.from-address=sender@outlook.com",
                "platform.delivery.email.username=sender@outlook.com",
                "platform.delivery.email.credential=test-only-app-password")
                .run(application -> {
                    assertThat(application).hasFailed();
                    assertThat(application.getStartupFailure()).hasMessageContaining("auth-mode");
                });
    }
}
