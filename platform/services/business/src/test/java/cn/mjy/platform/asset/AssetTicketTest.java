package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * 取件票（ADR 0019 决定 4）：签名覆盖租户、资产、版本与过期时刻，改任何一项都验不过。
 * 这里只测纯函数部分，端点行为见 {@link AssetPublicFetchTest}。
 */
@AssetIntegrationTest
class AssetTicketTest {

    private static final Duration TTL = Duration.ofDays(1);

    @Autowired
    private AssetTickets tickets;

    private final TenantId tenant = TenantId.random();
    private final UUID assetId = UUID.randomUUID();
    private final Instant now = Instant.parse("2026-09-24T00:00:00Z");

    @Test
    void afreshTicketVerifies() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));

        assertThat(tickets.verify(tenant, assetId, 1, ticket.query(), now)).isEqualTo(AssetTickets.Verdict.VALID);
    }

    @Test
    void theSignatureIsNotGuessableFromTheUrlItself() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));

        assertThat(ticket.query().get("sig")).hasSizeGreaterThanOrEqualTo(43);
        assertThat(ticket.path()).isEqualTo("/a/" + tenant.value() + "/" + assetId + "/1");
    }

    @Test
    void aTicketForAnotherTenantDoesNotVerifyHere() {
        TenantId other = TenantId.random();
        AssetTicket ticket = tickets.mint(other, assetId, 1, now.plus(TTL));

        assertThat(tickets.verify(tenant, assetId, 1, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void aTicketForAnotherAssetDoesNotVerifyHere() {
        AssetTicket ticket = tickets.mint(tenant, UUID.randomUUID(), 1, now.plus(TTL));

        assertThat(tickets.verify(tenant, assetId, 1, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void aTicketForAnotherVersionDoesNotVerifyHere() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));

        assertThat(tickets.verify(tenant, assetId, 2, ticket.query(), now))
                .isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void movingTheExpiryForwardBreaksTheSignature() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));
        var tampered = new java.util.TreeMap<>(ticket.query());
        tampered.put("exp", Long.toString(now.plus(Duration.ofDays(3650)).getEpochSecond()));

        assertThat(tickets.verify(tenant, assetId, 1, tampered, now)).isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void flippingOneCharacterOfTheSignatureBreaksIt() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));
        var tampered = new java.util.TreeMap<>(ticket.query());
        String signature = tampered.get("sig");
        tampered.put("sig", (signature.charAt(0) == 'A' ? 'B' : 'A') + signature.substring(1));

        assertThat(tickets.verify(tenant, assetId, 1, tampered, now)).isEqualTo(AssetTickets.Verdict.BAD_SIGNATURE);
    }

    @Test
    void anExpiredTicketIsToldApartFromAForgedOne() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));

        assertThat(tickets.verify(tenant, assetId, 1, ticket.query(), now.plus(Duration.ofDays(2))))
                .isEqualTo(AssetTickets.Verdict.EXPIRED);
    }

    @Test
    void aMissingSignatureOrExpiryIsRejected() {
        AssetTicket ticket = tickets.mint(tenant, assetId, 1, now.plus(TTL));
        var withoutSig = new java.util.TreeMap<>(ticket.query());
        withoutSig.remove("sig");
        var withoutExp = new java.util.TreeMap<>(ticket.query());
        withoutExp.remove("exp");

        assertThat(tickets.verify(tenant, assetId, 1, withoutSig, now)).isNotEqualTo(AssetTickets.Verdict.VALID);
        assertThat(tickets.verify(tenant, assetId, 1, withoutExp, now)).isNotEqualTo(AssetTickets.Verdict.VALID);
        assertThat(tickets.verify(tenant, assetId, 1, java.util.Map.of(), now))
                .isNotEqualTo(AssetTickets.Verdict.VALID);
    }

    /** 两个租户的密钥互不相通：同样的资产与版本，签出来的名字必须不同。 */
    @Test
    void perTenantKeysProduceDifferentSignatures() {
        String mine = tickets.mint(tenant, assetId, 1, now.plus(TTL)).query().get("sig");
        String theirs = tickets.mint(TenantId.random(), assetId, 1, now.plus(TTL)).query().get("sig");

        assertThat(mine).isNotEqualTo(theirs);
    }
}
