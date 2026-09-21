package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.PhpEnvelope.COMPLETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.SAVED;
import static cn.mjy.platform.engine.support.PhpEnvelope.event;
import static cn.mjy.platform.engine.support.SignedEventRequests.body;
import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.PhpEnvelope;
import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 中继是"至少一次"：平台确认前进程中断、超时后重试，都会让同一批事件再次到达。
 * 收件箱按 eventId 去重，重复到达的事件回 2xx（让中继标记已投递），但不产生任何重复效果。
 */
@EngineEventsIntegrationTest
class EngineEventInboxTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private FakeEngineInstanceDirectory directory;

    @Autowired
    private EngineRows rows;

    private TenantId tenant;
    private String instance;
    private String generation;

    @BeforeEach
    void registerEngine() {
        tenant = TenantId.random();
        instance = directory.register(tenant);
        generation = UUID.randomUUID().toString();
    }

    @Test
    void aRedeliveredBatchIsAcknowledgedButHasNoSecondEffect() throws Exception {
        PhpEnvelope saved = event(SAVED).instance(instance).generation(generation).response(7);
        PhpEnvelope completed = event(COMPLETED).instance(instance).generation(generation).response(7);
        byte[] batch = body(saved, completed);

        mvc.perform(signed(instance, batch))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.received").value(2))
                .andExpect(jsonPath("$.accepted").value(2))
                .andExpect(jsonPath("$.duplicates").value(0));
        mvc.perform(signed(instance, batch))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(0))
                .andExpect(jsonPath("$.duplicates").value(2));

        assertThat(rows.inbox(tenant)).isEqualTo(2);
        assertThat(rows.projections(tenant)).isEqualTo(1);
        assertThat(rows.outbox(tenant)).isEqualTo(1);
    }

    @Test
    void theSameEventTwiceInOneBatchCountsOnce() throws Exception {
        PhpEnvelope completed = event(COMPLETED).instance(instance).generation(generation).response(8);

        mvc.perform(signed(completed, completed))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(1))
                .andExpect(jsonPath("$.duplicates").value(1));

        assertThat(rows.inbox(tenant)).isEqualTo(1);
        assertThat(rows.outbox(tenant)).isEqualTo(1);
    }

    @Test
    void aBatchOverlappingAnEarlierOneOnlyAppliesTheNewEvents() throws Exception {
        PhpEnvelope first = event(COMPLETED).instance(instance).generation(generation).response(9);
        PhpEnvelope second = event(COMPLETED).instance(instance).generation(generation).response(10);
        mvc.perform(signed(first)).andExpect(status().isOk());

        mvc.perform(signed(first, second))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(1))
                .andExpect(jsonPath("$.duplicates").value(1));

        assertThat(rows.projections(tenant)).isEqualTo(2);
        assertThat(rows.outbox(tenant)).isEqualTo(2);
    }

    @Test
    void aSecondCompletionFactForTheSameResponseDoesNotEmitASecondOutboxRow() throws Exception {
        // 钩子与扫描器理论上只记一次完成；即便引擎侧因故产生两条不同 eventId 的完成事实，
        // 投影状态不变，发件箱也不重复。
        PhpEnvelope byHook = event(COMPLETED).instance(instance).generation(generation).response(11);
        PhpEnvelope byScanner = event(COMPLETED).instance(instance).generation(generation).response(11)
                .source("scanner");

        mvc.perform(signed(byHook, byScanner))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(2));

        assertThat(rows.inbox(tenant)).isEqualTo(2);
        assertThat(rows.outbox(tenant)).isEqualTo(1);
    }
}
