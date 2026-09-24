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
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;

/**
 * 匿名取件端点 {@code GET /a/{租户}/{资产}/{版本}}（ADR 0019 决定 4）。
 *
 * <p>要钉住的是三件事：拿着合法票的人取得到；一切验签失败返回<b>逐字节相同</b>的 404；
 * 拿着票<b>写不了</b>任何东西。
 */
@AssetIntegrationTest
@AutoConfigureMockMvc
class AssetPublicFetchTest {

    private static final String NOT_FOUND_BODY = "{\"error\":\"not_found\"}";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private AssetService assets;

    @Autowired
    private AssetTickets tickets;

    private TenantContext owner;
    private AssetView asset;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
        asset = assets.create(owner, "封面", new AssetUpload("image/png", "cover.png", AssetFixture.png()));
    }

    @Test
    void aValidTicketFetchesTheBytes() throws Exception {
        fetch(owner.tenantId().value(), asset.id(), 1, validTicket())
                .andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.IMAGE_PNG))
                .andExpect(header().string("X-Content-Type-Options", "nosniff"))
                .andExpect(header().string("Content-Security-Policy", "default-src 'none'; sandbox"))
                .andExpect(header().string("Cache-Control", org.hamcrest.Matchers.containsString("private")))
                .andExpect(content().bytes(AssetFixture.png()));
    }

    @Test
    void noTicketAtAllIsANotFound() throws Exception {
        expectUniformNotFound(fetch(owner.tenantId().value(), asset.id(), 1, Map.of()));
    }

    @Test
    void aForgedSignatureIsANotFound() throws Exception {
        Map<String, String> tampered = new TreeMap<>(validTicket());
        tampered.put("sig", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");

        expectUniformNotFound(fetch(owner.tenantId().value(), asset.id(), 1, tampered));
    }

    /** 换租户：签名覆盖租户，改了就验不过，而且与"资产不存在"的应答一模一样。 */
    @Test
    void aTicketReaimedAtAnotherTenantIsANotFound() throws Exception {
        TenantContext stranger = fixture.newTenant();

        expectUniformNotFound(fetch(stranger.tenantId().value(), asset.id(), 1, validTicket()));
    }

    @Test
    void aTicketReaimedAtAnotherAssetIsANotFound() throws Exception {
        AssetView other = assets.create(owner, "别的", new AssetUpload("image/gif", "b.gif", AssetFixture.gif()));

        expectUniformNotFound(fetch(owner.tenantId().value(), other.id(), 1, validTicket()));
    }

    @Test
    void guessingAnAssetIdIsANotFound() throws Exception {
        expectUniformNotFound(fetch(owner.tenantId().value(), UUID.randomUUID(), 1, validTicket()));
    }

    /** 签名对、版本不存在：也是同一个 404——不能靠它数出这件资产有几版。 */
    @Test
    void aTicketForAVersionThatWasNeverUploadedIsANotFound() throws Exception {
        Map<String, String> ticket = tickets.mint(owner.tenantId(), asset.id(), 2, later()).query();

        expectUniformNotFound(fetch(owner.tenantId().value(), asset.id(), 2, ticket));
    }

    /** 签名对但过期：410。走到这一步的人本来就持有过合法链接，告诉他"过期了"不泄漏新东西。 */
    @Test
    void anExpiredTicketIsGone() throws Exception {
        Map<String, String> ticket = tickets
                .mint(owner.tenantId(), asset.id(), 1, Instant.now().minus(Duration.ofMinutes(1)))
                .query();

        fetch(owner.tenantId().value(), asset.id(), 1, ticket)
                .andExpect(status().isGone())
                .andExpect(header().string("Cache-Control", "no-store"));
    }

    /** 路径上的租户不是 UUID：同样的 404，不能靠报错形状区分"格式不对"与"没有这件东西"。 */
    @Test
    void aMalformedPathIsTheSameNotFound() throws Exception {
        expectUniformNotFound(mvc.perform(get("/a/not-a-uuid/{id}/1", asset.id())
                .param("exp", validTicket().get("exp"))
                .param("sig", validTicket().get("sig"))));
    }

    /** 取件票只开了一个只读端点：拿着它创建、改、删都到不了任何地方。 */
    @Test
    void aTicketCannotBeUsedToWriteAnything() throws Exception {
        Map<String, String> ticket = validTicket();

        mvc.perform(post("/a/{t}/{id}/1", owner.tenantId().value(), asset.id())
                        .param("exp", ticket.get("exp")).param("sig", ticket.get("sig")))
                .andExpect(result -> assertThat(result.getResponse().getStatus()).isNotIn(200, 201, 202));
        mvc.perform(delete("/a/{t}/{id}/1", owner.tenantId().value(), asset.id())
                        .param("exp", ticket.get("exp")).param("sig", ticket.get("sig")))
                .andExpect(result -> assertThat(result.getResponse().getStatus()).isNotIn(200, 204));
        // 作者端点仍然需要令牌：票据不是身份。
        mvc.perform(post("/v1/assets").param("exp", ticket.get("exp")).param("sig", ticket.get("sig")))
                .andExpect(status().isUnauthorized());
    }

    private Map<String, String> validTicket() {
        return tickets.mint(owner.tenantId(), asset.id(), 1, later()).query();
    }

    private static Instant later() {
        return Instant.now().plus(Duration.ofDays(1));
    }

    private ResultActions fetch(UUID tenant, UUID assetId, int version, Map<String, String> ticket) throws Exception {
        var request = get("/a/{t}/{id}/{v}", tenant, assetId, version);
        ticket.forEach(request::param);
        return mvc.perform(request);
    }

    /** 验签及其之前的一切失败都必须返回逐字节相同的应答。 */
    private static void expectUniformNotFound(ResultActions actions) throws Exception {
        actions.andExpect(status().isNotFound())
                .andExpect(content().string(NOT_FOUND_BODY))
                .andExpect(header().string("Cache-Control", "no-store"));
    }
}
