package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.engine.ResponseKey;
import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.RespondentSource;
import cn.mjy.platform.response.ResponseAnswersUnavailableException;
import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/**
 * 经网关回读"这份答卷是用哪个邀请码答的"（ADR 0016 缺口 (b)）。
 *
 * <p>最要命的一条：读不到<b>不等于</b>没答完。把读取失败当成"没有完成记录"，
 * 催答就会朝一批其实已经答完的人发提醒。
 */
class GatewayRespondentIdentityResolverTest {

    private static final TenantId TENANT = TenantId.of(UUID.randomUUID().toString());
    private static final String INSTANCE = "hd-engine-01";
    private static final long SID = 900001L;

    /** 记下被问到的参数，并按剧本作答。 */
    private static final class RecordingSource implements RespondentSource {

        private final Map<Long, String> tokens;
        private final RuntimeException failure;
        private final List<List<Long>> asked = new ArrayList<>();

        RecordingSource(Map<Long, String> tokens, RuntimeException failure) {
            this.tokens = tokens;
            this.failure = failure;
        }

        @Override
        public Map<Long, String> readRespondents(String engineInstanceId, long engineSid, List<Long> responseIds) {
            asked.add(List.copyOf(responseIds));
            if (failure != null) {
                throw failure;
            }
            return tokens;
        }
    }

    private static ResponseProjection completed(long responseId) {
        return new ResponseProjection(new ResponseKey(INSTANCE, SID, "g1", responseId),
                ResponseState.ENGINE_COMPLETED, Instant.EPOCH, Instant.EPOCH, null, UUID.randomUUID());
    }

    @Test
    void itAsksTheGatewayOnceForTheWholePage() {
        RecordingSource source = new RecordingSource(Map.of(1L, "tok-a", 2L, "tok-b"), null);

        Map<Long, String> keys = new GatewayRespondentIdentityResolver(source)
                .respondentKeysOf(TENANT, List.of(completed(1), completed(2)));

        assertThat(source.asked).containsExactly(List.of(1L, 2L));
        assertThat(keys).containsOnlyKeys(1L, 2L);
    }

    @Test
    void anUnreachableGatewayIsNotReadAsNobodyHavingAnswered() {
        RecordingSource source = new RecordingSource(Map.of(),
                new ResponseAnswersUnavailableException("gateway timed out"));

        Map<Long, String> keys = new GatewayRespondentIdentityResolver(source)
                .respondentKeysOf(TENANT, List.of(completed(1)));

        // 空映射＝这一轮认不出任何人：对账跳过、不登记、下一轮重来。
        assertThat(keys).isEmpty();
    }

    @Test
    void anEmptyPageNeverReachesTheGateway() {
        RecordingSource source = new RecordingSource(Map.of(), null);

        assertThat(new GatewayRespondentIdentityResolver(source).respondentKeysOf(TENANT, List.of())).isEmpty();
        assertThat(source.asked).isEmpty();
    }

    @Test
    void aResponseTheGatewayCannotIdentifyIsLeftOut() {
        // 匿名问卷、或没用邀请码进场：网关回 null，读取客户端已经把它剔掉。
        RecordingSource source = new RecordingSource(Map.of(1L, "tok-a"), null);

        Map<Long, String> keys = new GatewayRespondentIdentityResolver(source)
                .respondentKeysOf(TENANT, List.of(completed(1), completed(2)));

        assertThat(keys).containsOnlyKeys(1L);
    }
}
