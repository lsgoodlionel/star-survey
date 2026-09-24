package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.SignedParameters;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * 绑定作答者的取件票（ADR 0019 决定 9）。这里只测纯函数部分，端点行为见
 * {@link RespondentAssetFetchTest}。
 *
 * <p>要钉住的核心是一句话：<b>乙持有自己那份合法票据与令牌，凑不出一张取得到甲的文件的票</b>。
 * 签名上下文覆盖租户、资产、版本与<b>作答者指纹</b>四者，改任何一项都验不过。
 */
@AssetIntegrationTest
class RespondentTicketTest {

    private static final Duration TTL = Duration.ofDays(1);
    private static final String ALICE = "invite-code-alice";
    private static final String BOB = "invite-code-bob";

    @Autowired
    private AssetTickets tickets;

    private final TenantId tenant = TenantId.random();
    private final UUID assetId = UUID.randomUUID();
    private final Instant now = Instant.parse("2026-09-24T00:00:00Z");

    @Test
    void aFreshRespondentTicketVerifiesForItsOwnRespondent() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, assetId, 1, ALICE, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, ALICE, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.VALID);
    }

    /** 本决定存在的理由：甲的票配上乙的令牌，取不到东西。 */
    @Test
    void anotherRespondentsTokenDoesNotVerifyAliceSticket() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, assetId, 1, ALICE, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, BOB, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    /** 不出示令牌就凑不出上下文：链接本身不足以取件，这正是与 bearer 票的分界。 */
    @Test
    void aRespondentTicketWithoutAtokenDoesNotVerify() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, assetId, 1, ALICE, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, "", ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    /** 令牌不写进链接：否则转发链接就等于转发身份，票据又退回 bearer。 */
    @Test
    void theTokenIsNotCarriedInTheTicketItself() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, assetId, 1, ALICE, now.plus(TTL));

        assertThat(ticket.query()).containsOnlyKeys(SignedParameters.EXPIRY_PARAM,
                SignedParameters.SIGNATURE_PARAM);
        assertThat(ticket.url("https://survey.example")).doesNotContain(ALICE);
        assertThat(ticket.path()).isEqualTo("/a/r/" + tenant.value() + "/" + assetId + "/1");
    }

    @Test
    void aRespondentTicketForAnotherAssetDoesNotVerifyHere() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, UUID.randomUUID(), 1, ALICE, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, ALICE, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void aRespondentTicketForAnotherTenantDoesNotVerifyHere() {
        AssetTicket ticket = tickets.mintForRespondent(TenantId.random(), assetId, 1, ALICE, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, ALICE, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void aRespondentTicketForAnotherVersionDoesNotVerifyHere() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, assetId, 2, ALICE, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, ALICE, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void anExpiredRespondentTicketIsRecognisedAsExpiredNotForged() {
        AssetTicket ticket = tickets.mintForRespondent(tenant, assetId, 1, ALICE, now.minusSeconds(1));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, ALICE, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.EXPIRED);
    }

    /** bearer 票与绑定票是两套上下文：一张不能当另一张用，两个方向都要挡。 */
    @Test
    void abearerTicketIsNotARespondentTicket() {
        AssetTicket bearer = tickets.mint(tenant, assetId, 1, now.plus(TTL));

        assertThat(tickets.verifyForRespondent(tenant, assetId, 1, ALICE, bearer.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void arespondentTicketIsNotAbearerTicket() {
        AssetTicket bound = tickets.mintForRespondent(tenant, assetId, 1, ALICE, now.plus(TTL));

        assertThat(tickets.verify(tenant, assetId, 1, bound.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    /** 指纹密钥与签名密钥分开派生：一个泄露推不出另一个。 */
    @Test
    void theFingerprintIsTenantScopedAndIrreversible() {
        String here = tickets.fingerprint(tenant, ALICE);
        String elsewhere = tickets.fingerprint(TenantId.random(), ALICE);

        assertThat(here).matches("[0-9a-f]{64}").isNotEqualTo(elsewhere);
        assertThat(here).isNotEqualTo(tickets.fingerprint(tenant, BOB));
        assertThat(here).doesNotContain(ALICE);
    }

    /** {@code rt} 是保留参数名：谁也不能把它当业务参数签进链接里。 */
    @Test
    void theRespondentTokenParameterNameIsReserved() {
        assertThat(SignedParameters.isReserved(SignedParameters.RESPONDENT_PARAM)).isTrue();
    }
}
