package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.PhpEnvelope.COMPLETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.SAVED;
import static cn.mjy.platform.engine.support.PhpEnvelope.event;
import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.SignedEventRequests;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 租户只由引擎实例目录判定，事件体里自称的租户一律无效；未登记的实例整批拒收、一行不写；
 * 批次里每个事件的实例必须等于验签确认的实例（请求头），否则整批拒收——实例 X 不能用自己的密钥替 Y 写事件；
 * 行级安全保证 B 既读不到 A 的收件箱/投影/发件箱，也写不进去。
 */
@EngineEventsIntegrationTest
class EngineEventTenancyTest {

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
    private EngineEventInbox inbox;

    @Autowired
    private ResponseProjectionRepository projections;

    @Autowired
    private EngineOutboxRepository outbox;

    private TenantId tenantA;
    private TenantId tenantB;
    private String instanceA;
    private String instanceB;
    private String generation;

    @BeforeEach
    void registerTwoTenants() {
        tenantA = TenantId.random();
        tenantB = TenantId.random();
        instanceA = directory.register(tenantA);
        instanceB = directory.register(tenantB);
        generation = UUID.randomUUID().toString();
    }

    @Test
    void aBatchFromAnUnknownEngineInstanceIsRejectedWithoutWritingAnything() throws Exception {
        // 签名本身正确（实例密钥由主密钥派生，与是否登记无关），但实例未登记：整批拒收。
        String unregistered = "engine-never-registered-" + UUID.randomUUID();

        mvc.perform(signed(
                        event(COMPLETED).instance(unregistered).generation(generation).response(1),
                        event(COMPLETED).instance(unregistered).generation(generation).response(2)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.error").value("unknown_engine_instance"));
    }

    @Test
    void anAuthenticatedInstanceCannotSmuggleEventsOfAnotherInstanceIntoItsBatch() throws Exception {
        // B 的引擎用自己的密钥正确签名（头 = B），但批次里夹带了一条自称 A 的事件：整批拒收，两边一行不写。
        mvc.perform(signed(
                        event(SAVED).instance(instanceB).survey(SURVEY).generation(generation).response(3),
                        event(COMPLETED).instance(instanceA).survey(SURVEY).generation(generation).response(3)))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("engine_instance_mismatch"));

        for (TenantId tenant : new TenantId[] {tenantA, tenantB}) {
            assertThat(rows.inbox(tenant)).isZero();
            assertThat(rows.projections(tenant)).isZero();
            assertThat(rows.outbox(tenant)).isZero();
        }
    }

    @Test
    void aBatchWhoseEventsAllBelongToAnotherInstanceThanTheSignerIsRejected() throws Exception {
        // B 用自己的密钥签名，头也写 B，但全部事件都自称 A。
        byte[] body = SignedEventRequests.body(
                event(COMPLETED).instance(instanceA).survey(SURVEY).generation(generation).response(4));

        mvc.perform(SignedEventRequests.signed(instanceB, body))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("engine_instance_mismatch"));

        assertThat(rows.inbox(tenantA)).isZero();
        assertThat(rows.projections(tenantA)).isZero();
    }

    @Test
    void aTenantClaimedInsideThePayloadIsIgnored() throws Exception {
        mvc.perform(signed(event(SAVED).instance(instanceA).survey(SURVEY).generation(generation).response(1)))
                .andExpect(status().isOk());

        // B 的引擎用与 A 完全相同的问卷/代次/答卷号，并在事件里自称属于 A。
        mvc.perform(signed(event(COMPLETED).instance(instanceB).survey(SURVEY).generation(generation).response(1)
                        .with("tenantId", tenantA.toString())
                        .with("tenant_id", tenantA.toString())))
                .andExpect(status().isOk());

        assertThat(rows.states(tenantA, instanceA, SURVEY, generation, 1)).containsExactly("in_progress");
        assertThat(rows.projections(tenantA)).isEqualTo(1);
        assertThat(rows.outbox(tenantA)).isZero();
        assertThat(rows.states(tenantB, instanceB, SURVEY, generation, 1)).containsExactly("engine_completed");
    }

    @Test
    void tenantBCannotReadTenantARows() throws Exception {
        mvc.perform(signed(event(COMPLETED).instance(instanceA).survey(SURVEY).generation(generation).response(4)))
                .andExpect(status().isOk());

        assertThat(rows.inbox(tenantB)).isZero();
        assertThat(rows.projections(tenantB)).isZero();
        assertThat(rows.outbox(tenantB)).isZero();
        assertThat(scope.call(tenantB, () -> projections.find(key(instanceA, 4)))).isEmpty();
        assertThat(scope.call(tenantA, () -> projections.find(key(instanceA, 4)))).isPresent();
    }

    @Test
    void theDatabaseRejectsWritingTenantARowsFromTenantBScope() {
        EngineEvent forged = new EngineEvent(UUID.randomUUID(), EngineEventType.COMPLETED, instanceA, SURVEY,
                generation, 5, "hook", Instant.now());
        Transition transition = new Transition(null, ResponseState.ENGINE_COMPLETED);

        assertThatThrownBy(() -> scope.run(tenantB, () -> inbox.record(tenantA, forged)))
                .rootCause().hasMessageContaining("row-level security");
        assertThatThrownBy(() -> scope.run(tenantB, () -> projections.advance(tenantA, forged)))
                .rootCause().hasMessageContaining("row-level security");
        assertThatThrownBy(() -> scope.run(tenantB, () -> outbox.append(tenantA, forged, transition)))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void outsideATenantScopeNothingCanBeWritten() {
        EngineEvent event = new EngineEvent(UUID.randomUUID(), EngineEventType.SAVED, instanceA, SURVEY,
                generation, 6, "hook", Instant.now());

        assertThatThrownBy(() -> inbox.record(tenantA, event))
                .rootCause().hasMessageContaining("row-level security");
    }

    private ResponseKey key(String instance, long response) {
        return new ResponseKey(instance, SURVEY, generation, response);
    }
}
