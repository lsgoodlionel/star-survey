package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.TreeMap;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/**
 * 按作答者身份授权的取件端点 {@code GET /a/r/{租户}/{资产}/{版本}?exp&sig&rt}（ADR 0019 决定 9）。
 *
 * <p>本车道最要紧的一条断言在 {@link #bobCannotFetchAlicesRecordingWithHisOwnToken()}：
 * <b>甲的录音不能让乙凭链接取到</b>。其余用例守住与决定 4 相同的统一拒绝口径。
 */
@AssetIntegrationTest
@AutoConfigureMockMvc
class RespondentAssetFetchTest {

    private static final String NOT_FOUND_BODY = "{\"error\":\"not_found\"}";
    private static final String ALICE = "invite-code-alice";
    private static final String BOB = "invite-code-bob";
    private static final String INSTANCE = "engine-a";
    private static final long SID = 4242L;
    private static final String GENERATION = "gen-1";
    private static final String CODE = "Q1";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private ResponderAssetService responderAssets;

    @Autowired
    private AssetService assets;

    @Autowired
    private AssetTickets tickets;

    private TenantContext owner;
    private UUID alicesAsset;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
        alicesAsset = ingest(ALICE, 11L);
    }

    @Test
    void aliceFetchesHerOwnRecordingWithHerOwnToken() throws Exception {
        fetch(owner.tenantId().value(), alicesAsset, 1, ticketFor(alicesAsset), ALICE)
                .andExpect(status().isOk())
                .andExpect(header().string("X-Content-Type-Options", "nosniff"))
                .andExpect(header().string("Content-Security-Policy", "default-src 'none'; sandbox"))
                .andExpect(content().bytes(AssetFixture.webm()));
    }

    /**
     * 本切片存在的理由：乙持有自己那份合法票据与自己的令牌，
     * 无论怎么组合都取不到甲的文件。
     */
    @Test
    void bobCannotFetchAlicesRecordingWithHisOwnToken() throws Exception {
        UUID bobsAsset = ingest(BOB, 12L);
        Map<String, String> bobsTicket = ticketFor(bobsAsset);

        // 乙的票改投到甲的资产上
        expectUniformNotFound(fetch(owner.tenantId().value(), alicesAsset, 1, bobsTicket, BOB));
        // 甲的票配上乙的令牌
        expectUniformNotFound(fetch(owner.tenantId().value(), alicesAsset, 1, ticketFor(alicesAsset), BOB));
        // 乙的令牌配上甲的票、再指回乙自己的资产
        expectUniformNotFound(fetch(owner.tenantId().value(), bobsAsset, 1, ticketFor(alicesAsset), BOB));
        // 乙自己的那条路仍然是通的——挡住的是越界，不是他本人
        fetch(owner.tenantId().value(), bobsAsset, 1, bobsTicket, BOB).andExpect(status().isOk());
    }

    /** 不出示令牌，链接本身不足以取件。 */
    @Test
    void aticketWithoutAtokenIsANotFound() throws Exception {
        var request = get("/a/r/{t}/{id}/{v}", owner.tenantId().value(), alicesAsset, 1);
        ticketFor(alicesAsset).forEach(request::param);

        expectUniformNotFound(mvc.perform(request));
    }

    @Test
    void aforgedSignatureIsANotFound() throws Exception {
        Map<String, String> tampered = new TreeMap<>(ticketFor(alicesAsset));
        tampered.put("sig", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");

        expectUniformNotFound(fetch(owner.tenantId().value(), alicesAsset, 1, tampered, ALICE));
    }

    @Test
    void aticketReaimedAtAnotherTenantIsANotFound() throws Exception {
        TenantContext stranger = fixture.newTenant();

        expectUniformNotFound(fetch(stranger.tenantId().value(), alicesAsset, 1, ticketFor(alicesAsset), ALICE));
    }

    @Test
    void guessingAnAssetIdIsANotFound() throws Exception {
        expectUniformNotFound(fetch(owner.tenantId().value(), UUID.randomUUID(), 1, ticketFor(alicesAsset), ALICE));
    }

    @Test
    void amalformedPathIsTheSameNotFound() throws Exception {
        var request = get("/a/r/not-a-uuid/{id}/1", alicesAsset);
        ticketFor(alicesAsset).forEach(request::param);

        expectUniformNotFound(mvc.perform(request.param("rt", ALICE)));
    }

    /** 签名对但过期：410，与决定 4 同一个例外。 */
    @Test
    void anExpiredRespondentTicketIsGone() throws Exception {
        Map<String, String> stale = tickets
                .mintForRespondent(owner.tenantId(), alicesAsset, 1, ALICE, Instant.now().minusSeconds(60))
                .query();

        fetch(owner.tenantId().value(), alicesAsset, 1, stale, ALICE)
                .andExpect(status().isGone())
                .andExpect(header().string("Cache-Control", "no-store"));
    }

    /** 两条取件路各管各的：bearer 那条路取不到作答者资产。 */
    @Test
    void thebearerFetchPathCannotReachAresponderAsset() throws Exception {
        Map<String, String> bearer = tickets
                .mint(owner.tenantId(), alicesAsset, 1, Instant.now().plus(Duration.ofDays(1)))
                .query();
        var request = get("/a/{t}/{id}/1", owner.tenantId().value(), alicesAsset);
        bearer.forEach(request::param);

        expectUniformNotFound(mvc.perform(request));
    }

    /** 反过来，作者素材也不该从绑定那条路取出来。 */
    @Test
    void therespondentFetchPathCannotReachAnAuthorAsset() throws Exception {
        AssetView authorAsset = assets.create(owner, "封面",
                new AssetUpload("image/png", "c.png", AssetFixture.png()));
        Map<String, String> bound = tickets
                .mintForRespondent(owner.tenantId(), authorAsset.id(), 1, ALICE, Instant.now().plus(Duration.ofDays(1)))
                .query();

        expectUniformNotFound(fetch(owner.tenantId().value(), authorAsset.id(), 1, bound, ALICE));
    }

    /** 绑定票同样只开了一个只读 GET：拿着它写不了任何东西。 */
    @Test
    void arespondentTicketCannotBeUsedToWriteAnything() throws Exception {
        Map<String, String> ticket = ticketFor(alicesAsset);

        mvc.perform(post("/a/r/{t}/{id}/1", owner.tenantId().value(), alicesAsset)
                        .param("exp", ticket.get("exp")).param("sig", ticket.get("sig")).param("rt", ALICE))
                .andExpect(result -> assertThat(result.getResponse().getStatus()).isNotIn(200, 201, 202));
        mvc.perform(delete("/a/r/{t}/{id}/1", owner.tenantId().value(), alicesAsset)
                        .param("exp", ticket.get("exp")).param("sig", ticket.get("sig")).param("rt", ALICE))
                .andExpect(result -> assertThat(result.getResponse().getStatus()).isNotIn(200, 204));
    }

    private UUID ingest(String token, long responseId) {
        UUID id = UUID.randomUUID();
        responderAssets.ingest(owner, new ResponderUpload(id, INSTANCE, SID, GENERATION, responseId, CODE,
                token, "audio/webm", "a.webm", AssetFixture.webm()));
        return id;
    }

    private Map<String, String> ticketFor(UUID assetId) {
        return responderAssets.mintTicket(owner, assetId, 1).query();
    }

    private ResultActions fetch(UUID tenant, UUID assetId, int version, Map<String, String> ticket, String token)
            throws Exception {
        var request = get("/a/r/{t}/{id}/{v}", tenant, assetId, version);
        ticket.forEach(request::param);
        return mvc.perform(request.param("rt", token));
    }

    private static void expectUniformNotFound(ResultActions actions) throws Exception {
        actions.andExpect(status().isNotFound())
                .andExpect(content().string(NOT_FOUND_BODY))
                .andExpect(header().string("Cache-Control", "no-store"));
    }
}
