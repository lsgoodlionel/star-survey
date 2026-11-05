package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.test.context.ConfigDataApplicationContextInitializer;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.SystemEnvironmentPropertySource;

/** The production Compose environment must reach the real application.yml binding. */
class OrgLoginEnvironmentBindingTest {

    @Configuration(proxyBeanMethods = false)
    @EnableConfigurationProperties(OrgLoginProperties.class)
    static class PropertiesConfiguration {
    }

    @Test
    void composeEnvironmentReachesTheOrganizationHandoffConfiguration() {
        new ApplicationContextRunner()
                .withInitializer(context -> context.getEnvironment().getPropertySources().addFirst(
                        new SystemEnvironmentPropertySource("compose-fixture", Map.of(
                                "PLATFORM_PUBLIC_BASE_URL", "https://survey.example.test",
                                "PLATFORM_ADMIN_WEB_BASE_URL", "https://survey.example.test"))))
                .withInitializer(new ConfigDataApplicationContextInitializer())
                .withUserConfiguration(PropertiesConfiguration.class)
                .run(context -> {
                    assertThat(context).hasNotFailed();
                    OrgLoginProperties properties = context.getBean(OrgLoginProperties.class);
                    assertThat(properties.callbackBaseUrl()).isEqualTo("https://survey.example.test");
                    assertThat(properties.adminWebBaseUrl()).isEqualTo("https://survey.example.test");
                    assertThat(properties.isAdminWebConfigured()).isTrue();
                });
    }
}
