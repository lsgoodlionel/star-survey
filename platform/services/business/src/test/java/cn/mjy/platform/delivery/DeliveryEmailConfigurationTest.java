package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

/** 配置回归：始终注册租户级 Email provider，但没有租户配置或静态回退时保持不可用。 */
class DeliveryEmailConfigurationTest {

    private final ApplicationContextRunner context = new ApplicationContextRunner()
            .withUserConfiguration(DeliveryConfig.class, DeliveryEmailConfig.class)
            .withBean(EmailSettingsService.class, () -> mock(EmailSettingsService.class));

    @Test
    void remainsFailClosedWhenEmailIsNotExplicitlyEnabled() {
        context.run(application -> {
            assertThat(application).hasNotFailed();
            assertThat(application.getBeansOfType(DeliveryProvider.class).values())
                    .singleElement()
                    .satisfies(provider -> {
                        assertThat(provider.channel()).isEqualTo(DeliveryChannel.EMAIL);
                        assertThat(provider.name()).isEqualTo("smtp-configured");
                    });
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
