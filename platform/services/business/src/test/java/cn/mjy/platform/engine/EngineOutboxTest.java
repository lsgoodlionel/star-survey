package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.PhpEnvelope.COMPLETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.SAVED;
import static cn.mjy.platform.engine.support.PhpEnvelope.event;
import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doCallRealMethod;
import static org.mockito.Mockito.doThrow;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.PhpEnvelope;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 发件箱与投影变更同一事务：发件箱写不进去时，投影与收件箱一起回滚，
 * 平台回非 2xx，中继重投后一切正常——下游既不会漏消息，也不会收到"投影没变却有消息"的幽灵。
 */
@EngineEventsIntegrationTest
class EngineOutboxTest {

    private static final int SURVEY = 880001;

    @Autowired
    private MockMvc mvc;

    @Autowired
    private FakeEngineInstanceDirectory directory;

    @Autowired
    private EngineRows rows;

    @Autowired
    private TenantScope scope;

    @Autowired
    private JdbcClient jdbc;

    @MockitoSpyBean
    private EngineOutboxRepository outbox;

    private TenantId tenant;
    private String instance;
    private String generation;

    @BeforeEach
    void registerEngine() {
        tenant = TenantId.random();
        instance = directory.register(tenant);
        generation = UUID.randomUUID().toString();
    }

    @AfterEach
    void restoreOutbox() {
        doCallRealMethod().when(outbox).append(any(), any(), any());
    }

    @Test
    void theOutboxRowDescribesTheCompletedResponse() throws Exception {
        PhpEnvelope completed = envelope(COMPLETED, 1).occurredAt("2026-09-21 07:30:00").source("scanner");

        mvc.perform(signed(completed)).andExpect(status().isOk());

        Map<String, Object> row = scope.call(tenant, () -> jdbc.sql("""
                SELECT event_type, engine_instance_id, survey_id, generation, response_id, previous_state,
                       source_event_id::text AS source_event_id, published_at,
                       payload->>'state' AS payload_state, payload->>'occurredAt' AS payload_occurred_at,
                       payload->>'tenantId' AS payload_tenant, payload->>'source' AS payload_source
                FROM engine_outbox
                """).query().singleRow());
        assertThat(row).containsEntry("event_type", "response.engine_completed")
                .containsEntry("engine_instance_id", instance)
                .containsEntry("survey_id", (long) SURVEY)
                .containsEntry("generation", generation)
                .containsEntry("response_id", 1L)
                .containsEntry("previous_state", null)
                .containsEntry("source_event_id", completed.eventId())
                .containsEntry("published_at", null)
                .containsEntry("payload_state", "engine_completed")
                .containsEntry("payload_occurred_at", "2026-09-21T07:30:00Z")
                .containsEntry("payload_tenant", tenant.toString())
                .containsEntry("payload_source", "scanner");
    }

    @Test
    void whenTheOutboxWriteFailsTheProjectionAndInboxRollBackAndRedeliveryRecovers() throws Exception {
        PhpEnvelope saved = envelope(SAVED, 2);
        PhpEnvelope completed = envelope(COMPLETED, 2);
        mvc.perform(signed(saved)).andExpect(status().isOk());
        doThrow(new DataAccessResourceFailureException("simulated outbox failure"))
                .when(outbox).append(any(), any(), any());

        mvc.perform(signed(completed))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("temporarily_unavailable"));

        assertThat(rows.states(tenant, instance, SURVEY, generation, 2)).containsExactly("in_progress");
        assertThat(rows.inbox(tenant)).isEqualTo(1);
        assertThat(rows.outbox(tenant)).isZero();

        doCallRealMethod().when(outbox).append(any(), any(), any());
        mvc.perform(signed(completed))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(1));

        assertThat(rows.states(tenant, instance, SURVEY, generation, 2)).containsExactly("engine_completed");
        assertThat(rows.outbox(tenant)).isEqualTo(1);
    }

    @Test
    void theRuntimeRoleCannotDeleteOutboxOrProjectionRowsNorRewriteTheInbox() throws Exception {
        mvc.perform(signed(envelope(COMPLETED, 3))).andExpect(status().isOk());

        assertThat(privilege("engine_outbox", "DELETE")).isFalse();
        assertThat(privilege("response_projection", "DELETE")).isFalse();
        assertThat(privilege("engine_event_inbox", "UPDATE")).isFalse();
        assertThat(privilege("engine_event_inbox", "DELETE")).isFalse();
        assertThat(privilege("engine_outbox", "UPDATE")).isTrue();
    }

    private boolean privilege(String table, String privilege) {
        return jdbc.sql("SELECT has_table_privilege(current_user, :table, :privilege)")
                .param("table", table)
                .param("privilege", privilege)
                .query(Boolean.class).single();
    }

    private PhpEnvelope envelope(String type, int response) {
        return event(type).instance(instance).survey(SURVEY).generation(generation).response(response);
    }
}
