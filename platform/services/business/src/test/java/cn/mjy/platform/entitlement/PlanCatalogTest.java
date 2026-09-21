package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 套餐是配置不是代码（U-07）：每个能力是独立开关；已发布的版本不可修改，改动只能发布新版本。 */
@SpringBootTest
class PlanCatalogTest {

    @Autowired
    private PlanCatalog plans;

    @Autowired
    private JdbcClient jdbc;

    @Value("${spring.flyway.url}")
    private String flywayUrl;

    @Value("${spring.flyway.user}")
    private String flywayUser;

    @Value("${spring.flyway.password}")
    private String flywayPassword;

    @Test
    void publishingTheSamePlanAgainCreatesANewVersionAndLeavesTheOldOneIntact() {
        String code = EntitlementFixtures.uniquePlanCode();
        PlanVersion v1 = plans.publish(new PlanDefinition(code,
                Set.of(Capabilities.SURVEY_READ), Map.of(Meters.VALID_COMPLETED_RESPONSE, 100_000L), 30));
        PlanVersion v2 = plans.publish(new PlanDefinition(code,
                Set.of(Capabilities.SURVEY_READ, Capabilities.RESPONSE_EXPORT),
                Map.of(Meters.VALID_COMPLETED_RESPONSE, 200_000L), 60));

        assertThat(v1.version()).isEqualTo(1);
        assertThat(v2.version()).isEqualTo(2);
        assertThat(v2.id()).isNotEqualTo(v1.id());
        PlanVersion reloaded = plans.find(v1.id()).orElseThrow();
        assertThat(reloaded.capabilities()).containsExactly(Capabilities.SURVEY_READ);
        assertThat(reloaded.quotaFor(Meters.VALID_COMPLETED_RESPONSE)).hasValue(100_000L);
        assertThat(reloaded.exportWindowDays()).isEqualTo(30);
        assertThat(plans.versionsOf(code)).extracting(PlanVersion::version).containsExactly(1, 2);
    }

    @Test
    void everyCapabilityIsAnIndependentSwitch() {
        PlanVersion plan = plans.publish(new PlanDefinition(EntitlementFixtures.uniquePlanCode(),
                Set.of(Capabilities.RESPONSE_EXPORT), Map.of(), 30));

        assertThat(plan.enables(Capabilities.RESPONSE_EXPORT)).isTrue();
        assertThat(plan.enables(Capabilities.SURVEY_READ)).isFalse();
        assertThat(plan.quotaFor(Meters.AI_TOKENS)).isEmpty();
    }

    @Test
    void aPublishedVersionCannotBeUpdatedOrDeletedEvenBypassingTheService() {
        PlanVersion plan = plans.publish(new PlanDefinition(EntitlementFixtures.uniquePlanCode(),
                Set.of(Capabilities.SURVEY_READ), Map.of(Meters.AI_TOKENS, 10L), 30));

        assertThatThrownBy(() -> jdbc.sql("UPDATE plan_version SET export_window_days = 999 WHERE id = :id")
                .param("id", plan.id()).update())
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> jdbc.sql("DELETE FROM plan_version WHERE id = :id")
                .param("id", plan.id()).update())
                .rootCause().hasMessageContaining("permission denied");
        assertThat(plans.find(plan.id()).orElseThrow().exportWindowDays()).isEqualTo(30);
    }

    @Test
    void theImmutabilityTriggerAlsoStopsTheTableOwner() throws Exception {
        PlanVersion plan = plans.publish(new PlanDefinition(EntitlementFixtures.uniquePlanCode(),
                Set.of(Capabilities.SURVEY_READ), Map.of(), 30));

        try (Connection owner = DriverManager.getConnection(flywayUrl, flywayUser, flywayPassword);
             Statement statement = owner.createStatement()) {
            assertThatThrownBy(() -> statement.executeUpdate(
                    "UPDATE plan_version SET export_window_days = 999 WHERE id = '" + plan.id() + "'"))
                    .hasMessageContaining("plan_version is immutable");
            assertThatThrownBy(() -> statement.executeUpdate(
                    "DELETE FROM plan_version WHERE id = '" + plan.id() + "'"))
                    .hasMessageContaining("plan_version is immutable");
        }
    }

    @Test
    void unknownCapabilitiesAndMetersAreRejectedAtPublishTime() {
        String code = EntitlementFixtures.uniquePlanCode();

        assertThatThrownBy(() -> plans.publish(new PlanDefinition(code, Set.of("survey.teleport"), Map.of(), 30)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("survey.teleport");
        assertThatThrownBy(() -> plans.publish(new PlanDefinition(code, Set.of(), Map.of("coins", 1L), 30)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("coins");
        assertThatThrownBy(() -> plans.publish(new PlanDefinition(code, Set.of(),
                Map.of(Meters.AI_TOKENS, -1L), 30)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void theValidCompletedResponseMeterIsDefinedPerSurveyAndNeverResets() {
        MeterDefinition meter = plans.meter(Meters.VALID_COMPLETED_RESPONSE).orElseThrow();

        assertThat(meter.scope()).isEqualTo(MeterScope.SURVEY);
        assertThat(meter.resetPolicy()).isEqualTo(ResetPolicy.NEVER);
        assertThat(plans.capability(Capabilities.RESPONSE_COLLECT).orElseThrow().meter())
                .isEqualTo(Meters.VALID_COMPLETED_RESPONSE);
    }
}
