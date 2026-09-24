package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * 「平台经网关把作答者上传拉进资产库」这一整条（ADR 0019 决定 8 ＋ 决定 9）。
 *
 * <p>这里钉住的是接线本身：清单驱动、按答卷绑定作答者、重复拉取幂等、
 * 以及<b>任何一环读不到都失败关闭</b>——绝不留下一批「只拉进来一半」的资产而没人察觉。
 */
@AssetIntegrationTest
class ResponderUploadPullTest {

    private static final String INSTANCE = "engine-a";
    private static final long SID = 4242L;
    private static final String GENERATION = "gen-1";
    private static final String ALICE = "invite-code-alice";
    private static final String BOB = "invite-code-bob";

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private ResponderAssetService responderAssets;

    @Autowired
    private AssetTickets tickets;

    private TenantContext owner;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
    }

    @Test
    void amanifestIsPulledIntoAssetsBoundToEachRespondent() {
        UUID alices = UUID.randomUUID();
        UUID bobs = UUID.randomUUID();
        FakeUploadSource source = new FakeUploadSource()
                .with(alices, 7L, "REC1", AssetFixture.webm())
                .with(bobs, 9L, "REC1", AssetFixture.webm());
        ResponderUploadPuller puller = puller(source, Map.of(7L, ALICE, 9L, BOB));

        ResponderUploadPullResult result = puller.pull(owner, INSTANCE, SID, GENERATION, List.of(7L, 9L));

        assertThat(result.ingested()).isEqualTo(2);
        assertThat(result.skipped()).isZero();
        assertThat(responderAssets.respondentKeyOf(owner, alices))
                .contains(tickets.fingerprint(owner.tenantId(), ALICE));
        assertThat(responderAssets.respondentKeyOf(owner, bobs))
                .contains(tickets.fingerprint(owner.tenantId(), BOB));
    }

    /** 再拉一遍不产生第二件资产，也不重新下载已经有的那些字节。 */
    @Test
    void asecondPullIsIdempotentAndDoesNotRefetchTheBytes() {
        UUID token = UUID.randomUUID();
        FakeUploadSource source = new FakeUploadSource().with(token, 7L, "REC1", AssetFixture.webm());
        ResponderUploadPuller puller = puller(source, Map.of(7L, ALICE));
        puller.pull(owner, INSTANCE, SID, GENERATION, List.of(7L));

        ResponderUploadPullResult again = puller.pull(owner, INSTANCE, SID, GENERATION, List.of(7L));

        assertThat(again.ingested()).isZero();
        assertThat(again.skipped()).isEqualTo(1);
        assertThat(source.contentCalls()).containsExactly(token);
        assertThat(responderAssets.forResponse(owner, INSTANCE, SID, GENERATION, 7L)).hasSize(1);
    }

    /** 认不出作答者（匿名卷、没用邀请码进场）：照样入库，但没有绑定，签不出票。 */
    @Test
    void anUploadWhoseRespondentIsUnknownIsIngestedWithoutAbinding() {
        UUID token = UUID.randomUUID();
        FakeUploadSource source = new FakeUploadSource().with(token, 7L, "REC1", AssetFixture.webm());

        puller(source, Map.of()).pull(owner, INSTANCE, SID, GENERATION, List.of(7L));

        assertThat(responderAssets.respondentKeyOf(owner, token)).isEmpty();
        assertThatThrownBy(() -> responderAssets.mintTicket(owner, token, 1))
                .isInstanceOf(AssetConflictException.class);
    }

    /** 清单读不到就整批失败：绝不把"读不到"解释成"这页没有上传"。 */
    @Test
    void amanifestFailureAbortsTheWholePull() {
        ResponderUploadPuller puller = puller(new ExplodingUploadSource(), Map.of(7L, ALICE));

        assertThatThrownBy(() -> puller.pull(owner, INSTANCE, SID, GENERATION, List.of(7L)))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    /** 过不了平台闸门的那一件不入库，但不该把整页拖下水——它是一件坏文件，不是一次读取故障。 */
    @Test
    void anUploadThatFailsTheGateIsReportedButDoesNotAbortThePage() {
        UUID good = UUID.randomUUID();
        UUID bad = UUID.randomUUID();
        FakeUploadSource source = new FakeUploadSource()
                .with(good, 7L, "REC1", AssetFixture.webm())
                .with(bad, 7L, "REC2", AssetFixture.svg());

        ResponderUploadPullResult result = puller(source, Map.of(7L, ALICE))
                .pull(owner, INSTANCE, SID, GENERATION, List.of(7L));

        assertThat(result.ingested()).isEqualTo(1);
        assertThat(result.refused()).isEqualTo(1);
        assertThat(responderAssets.forResponse(owner, INSTANCE, SID, GENERATION, 7L)).hasSize(1);
    }

    /** 空页不出网。 */
    @Test
    void anEmptyPageNeverLeavesTheProcess() {
        FakeUploadSource source = new FakeUploadSource();

        ResponderUploadPullResult result = puller(source, Map.of())
                .pull(owner, INSTANCE, SID, GENERATION, List.of());

        assertThat(result.ingested()).isZero();
        assertThat(source.manifestCalls()).isZero();
    }

    private ResponderUploadPuller puller(ResponderUploadSource source, Map<Long, String> respondents) {
        return new ResponderUploadPuller(source, (instance, sid, ids) -> respondents, responderAssets);
    }

    /** 假网关：按清单交付字节，并记下取过哪些 token。 */
    private static final class FakeUploadSource implements ResponderUploadSource {

        private final Map<UUID, ResponderUploadBytes> items = new LinkedHashMap<>();
        private final List<UUID> contentCalls = new ArrayList<>();
        private int manifestCalls;

        FakeUploadSource with(UUID token, long responseId, String questionCode, byte[] content) {
            ResponderUploadRef ref = new ResponderUploadRef(token, responseId, questionCode,
                    "a." + questionCode, "webm", content.length);
            items.put(token, new ResponderUploadBytes(ref, content));
            return this;
        }

        List<UUID> contentCalls() {
            return contentCalls;
        }

        int manifestCalls() {
            return manifestCalls;
        }

        @Override
        public List<ResponderUploadRef> manifest(String engineInstanceId, long engineSid, String generation,
                List<Long> responseIds) {
            manifestCalls++;
            return items.values().stream().map(ResponderUploadBytes::ref)
                    .filter(ref -> responseIds.contains(ref.responseId())).toList();
        }

        @Override
        public ResponderUploadBytes content(String engineInstanceId, long engineSid, String generation,
                UUID uploadToken) {
            contentCalls.add(uploadToken);
            return items.get(uploadToken);
        }
    }

    private static final class ExplodingUploadSource implements ResponderUploadSource {

        @Override
        public List<ResponderUploadRef> manifest(String engineInstanceId, long engineSid, String generation,
                List<Long> responseIds) {
            throw new ResponderUploadsUnavailableException("gateway is down");
        }

        @Override
        public ResponderUploadBytes content(String engineInstanceId, long engineSid, String generation,
                UUID uploadToken) {
            throw new ResponderUploadsUnavailableException("gateway is down");
        }
    }
}
