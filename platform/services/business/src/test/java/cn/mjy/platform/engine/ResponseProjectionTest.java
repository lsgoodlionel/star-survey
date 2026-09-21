package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.PhpEnvelope.COMPLETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.DELETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.SAVED;
import static cn.mjy.platform.engine.support.PhpEnvelope.event;
import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.PhpEnvelope;
import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 答卷投影：in_progress → engine_completed，另有终态 deleted。
 * 迟到或乱序的事件不得让状态倒退；代次不同的同号答卷是两份不同的答卷（ADR 0003）。
 * 进入 engine_completed / deleted 时，在同一事务里写发件箱。
 */
@EngineEventsIntegrationTest
class ResponseProjectionTest {

    private static final int SURVEY = 880001;

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
    void aSavedResponseIsInProgressAndEmitsNothingDownstream() throws Exception {
        mvc.perform(signed(envelope(SAVED, 1))).andExpect(status().isOk());

        assertThat(state(1)).containsExactly("in_progress");
        assertThat(rows.outbox(tenant)).isZero();
    }

    @Test
    void completingAResponseMovesItForwardAndEmitsOneOutboxRow() throws Exception {
        mvc.perform(signed(envelope(SAVED, 2))).andExpect(status().isOk());
        mvc.perform(signed(envelope(COMPLETED, 2))).andExpect(status().isOk());

        assertThat(state(2)).containsExactly("engine_completed");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.engine_completed");
    }

    @Test
    void aCompletionWithoutAnyEarlierSaveStillProjects() throws Exception {
        // 数据录入或保存事件丢失时，平台第一次听说这份答卷就是完成事件。
        mvc.perform(signed(envelope(COMPLETED, 3))).andExpect(status().isOk());

        assertThat(state(3)).containsExactly("engine_completed");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.engine_completed");
    }

    @Test
    void aLateSaveDoesNotRegressACompletedResponse() throws Exception {
        mvc.perform(signed(envelope(COMPLETED, 4))).andExpect(status().isOk());
        mvc.perform(signed(envelope(SAVED, 4))).andExpect(status().isOk());

        assertThat(state(4)).containsExactly("engine_completed");
        assertThat(rows.outbox(tenant)).isEqualTo(1);
    }

    @Test
    void outOfOrderEventsInsideOneBatchEndInTheNewestState() throws Exception {
        mvc.perform(signed(envelope(COMPLETED, 5), envelope(SAVED, 5))).andExpect(status().isOk());

        assertThat(state(5)).containsExactly("engine_completed");
    }

    @Test
    void deletionIsTerminalAndALateCompletionCannotResurrectTheResponse() throws Exception {
        mvc.perform(signed(envelope(SAVED, 6))).andExpect(status().isOk());
        mvc.perform(signed(envelope(DELETED, 6))).andExpect(status().isOk());
        mvc.perform(signed(envelope(COMPLETED, 6))).andExpect(status().isOk());
        mvc.perform(signed(envelope(SAVED, 6))).andExpect(status().isOk());

        assertThat(state(6)).containsExactly("deleted");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.deleted");
    }

    @Test
    void deletingACompletedResponseEmitsBothFactsInOrder() throws Exception {
        mvc.perform(signed(envelope(COMPLETED, 7))).andExpect(status().isOk());
        mvc.perform(signed(envelope(DELETED, 7))).andExpect(status().isOk());

        assertThat(state(7)).containsExactly("deleted");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.engine_completed", "response.deleted");
    }

    @Test
    void aTombstoneArrivingFirstIsKeptSoLaterEventsCannotRecreateTheResponse() throws Exception {
        mvc.perform(signed(envelope(DELETED, 8))).andExpect(status().isOk());
        mvc.perform(signed(envelope(SAVED, 8), envelope(COMPLETED, 8))).andExpect(status().isOk());

        assertThat(state(8)).containsExactly("deleted");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.deleted");
    }

    @Test
    void theSameResponseIdInANewGenerationIsANewResponse() throws Exception {
        // 停用再激活后 responses_<sid> 重建，id 从 1 重新计数；旧代次的完成不能吞掉新代次的答卷。
        String reactivated = UUID.randomUUID().toString();
        mvc.perform(signed(envelope(COMPLETED, 1))).andExpect(status().isOk());

        mvc.perform(signed(event(SAVED).instance(instance).survey(SURVEY).generation(reactivated).response(1)))
                .andExpect(status().isOk());

        assertThat(state(1)).containsExactly("engine_completed");
        assertThat(rows.states(tenant, instance, SURVEY, reactivated, 1)).containsExactly("in_progress");
        assertThat(rows.projections(tenant)).isEqualTo(2);
    }

    @Test
    void theSameResponseIdInAnotherSurveyIsANewResponse() throws Exception {
        mvc.perform(signed(envelope(COMPLETED, 1))).andExpect(status().isOk());
        mvc.perform(signed(event(SAVED).instance(instance).survey(SURVEY + 1).generation(generation).response(1)))
                .andExpect(status().isOk());

        assertThat(rows.projections(tenant)).isEqualTo(2);
    }

    private PhpEnvelope envelope(String type, int response) {
        return event(type).instance(instance).survey(SURVEY).generation(generation).response(response);
    }

    private List<String> state(int response) {
        return rows.states(tenant, instance, SURVEY, generation, response);
    }
}
