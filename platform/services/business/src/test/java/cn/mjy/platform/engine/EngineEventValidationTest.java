package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.PhpEnvelope.COMPLETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.SAVED;
import static cn.mjy.platform.engine.support.PhpEnvelope.event;
import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.shared.TenantId;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 格式不对或平台还不认识的事件整批拒收（非 2xx），引擎侧保持未投递、下轮重投，不静默丢弃；
 * 拒收时一行不写。
 */
@EngineEventsIntegrationTest
class EngineEventValidationTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private FakeEngineInstanceDirectory directory;

    @Autowired
    private EngineRows rows;

    private TenantId tenant;
    private String instance;

    @BeforeEach
    void registerEngine() {
        tenant = TenantId.random();
        instance = directory.register(tenant);
    }

    @Test
    void anEnvelopeMissingRequiredFieldsRejectsTheBatch() throws Exception {
        String body = "{\"events\":[{\"eventType\":\"response.saved\",\"engineInstanceId\":\"" + instance + "\"}]}";

        mvc.perform(signed(instance, body.getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_batch"));

        assertThat(rows.inbox(tenant)).isZero();
    }

    @Test
    void aBodyThatIsNotJsonIsRejected() throws Exception {
        mvc.perform(signed(instance, "not json".getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_batch"));
    }

    @Test
    void anUnsupportedSchemaVersionRejectsTheWholeBatch() throws Exception {
        mvc.perform(signed(
                        event(SAVED).instance(instance).response(1),
                        event(SAVED).instance(instance).response(2).schemaVersion(2)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("unsupported_schema_version"));

        assertThat(rows.inbox(tenant)).isZero();
        assertThat(rows.projections(tenant)).isZero();
    }

    @Test
    void anUnknownEventTypeRejectsTheWholeBatch() throws Exception {
        mvc.perform(signed(event("response.archived").instance(instance).response(1)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("unsupported_event_type"));

        assertThat(rows.inbox(tenant)).isZero();
    }

    @Test
    void anUnreadableOccurredAtRejectsTheBatch() throws Exception {
        mvc.perform(signed(event(SAVED).instance(instance).response(1).occurredAt("yesterday")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_occurred_at"));

        assertThat(rows.inbox(tenant)).isZero();
    }

    @Test
    void anIsoTimestampWithAnOffsetIsAlsoAccepted() throws Exception {
        String generation = UUID.randomUUID().toString();

        mvc.perform(signed(event(COMPLETED).instance(instance).generation(generation).response(1)
                        .occurredAt("2026-09-21T15:00:00+08:00")))
                .andExpect(status().isOk());

        assertThat(rows.states(tenant, instance, 880001, generation, 1)).containsExactly("engine_completed");
    }
}
