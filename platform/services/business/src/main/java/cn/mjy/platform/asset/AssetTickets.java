package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.SignedParameters;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import javax.crypto.Mac;
import org.springframework.stereotype.Component;

/**
 * 取件票的签发与验签（ADR 0019 决定 4）。
 *
 * <p>签名上下文覆盖<b>租户、资产、版本</b>三者：
 *
 * <pre>
 * asset/v1/&lt;租户 uuid&gt;/&lt;资产 uuid&gt;/&lt;版本号&gt;
 * </pre>
 *
 * 改其中任何一项都验不过——A 租户的票改不去取 B 租户的资产，第 1 版的票改不去取第 2 版。
 * 业务参数集合是<b>空的</b>：地址上除了 {@code exp} 与 {@code sig} 不该再有别的东西，
 * 多带一个参数就会落进签名比对里，直接变成 BAD_SIGNATURE。
 *
 * <p>签名机制本身不在这里，在 {@link SignedParameters}——投放链接与取件票共用同一份实现与同一批向量测试。
 */
@Component
public class AssetTickets {

    static final String PATH_PREFIX = "/a/";
    /** 绑定作答者那条路多一段，与 bearer 那条路径形状上就分开（ADR 0019 决定 9）。 */
    static final String RESPONDENT_SEGMENT = "r";

    /** 校验结论。{@link #VALID} 之外一律不许取到字节。 */
    public enum Verdict {
        VALID,
        EXPIRED,
        BAD_SIGNATURE
    }

    private final AssetKeys keys;

    AssetTickets(AssetKeys keys) {
        this.keys = keys;
    }

    public boolean isConfigured() {
        return keys.isConfigured();
    }

    public AssetTicket mint(TenantId tenant, UUID assetId, int versionNo, Instant expiresAt) {
        Map<String, String> signed = SignedParameters.sign(keys.fetchKey(tenant),
                context(tenant, assetId, versionNo), Map.of(), expiresAt);
        return new AssetTicket(path(tenant, assetId, versionNo), signed);
    }

    /**
     * 验签。主密钥没配时返回 {@link Verdict#BAD_SIGNATURE} 而不是抛异常——
     * 匿名可达面上"没配密钥"与"签名不对"必须是同一个结局（ADR 0018 决定 5）。
     */
    public Verdict verify(TenantId tenant, UUID assetId, int versionNo, Map<String, String> presented, Instant now) {
        if (!keys.isConfigured()) {
            return Verdict.BAD_SIGNATURE;
        }
        return translate(SignedParameters.verify(keys.fetchKey(tenant),
                context(tenant, assetId, versionNo), presented, now));
    }

    /**
     * 签一张<b>绑定作答者</b>的票（ADR 0019 决定 9）。
     *
     * <p>{@code respondentToken} 只参与指纹计算，<b>不进票据</b>：链接里因此没有令牌，
     * 转发链接不等于转发身份。取件方必须自己补上 {@code rt} 才凑得出同一个上下文。
     */
    public AssetTicket mintForRespondent(TenantId tenant, UUID assetId, int versionNo, String respondentToken,
            Instant expiresAt) {
        return mintForFingerprint(tenant, assetId, versionNo, fingerprint(tenant, respondentToken), expiresAt);
    }

    /**
     * 直接用已存的指纹签票（签发路径上根本不需要再见到令牌本身——平台库里也只有指纹）。
     */
    public AssetTicket mintForFingerprint(TenantId tenant, UUID assetId, int versionNo, String fingerprint,
            Instant expiresAt) {
        Map<String, String> signed = SignedParameters.sign(keys.fetchKey(tenant),
                respondentContext(tenant, assetId, versionNo, fingerprint), Map.of(), expiresAt);
        return new AssetTicket(respondentPath(tenant, assetId, versionNo), signed);
    }

    /**
     * 验一张绑定票。指纹在上下文里，所以<b>换一个令牌就是换一个上下文</b>，签名当场对不上——
     * 这正是"乙凑不出取得到甲的文件的票"那句话的实现。
     *
     * <p>{@code presented} 里<b>不得</b>含 {@code rt}：调用方在验签之前先把它摘出来
     * （见 {@link AssetRespondentController}）。多带一个参数会落进签名比对里，直接 BAD_SIGNATURE。
     */
    public Verdict verifyForRespondent(TenantId tenant, UUID assetId, int versionNo, String respondentToken,
            Map<String, String> presented, Instant now) {
        if (!keys.isConfigured()) {
            return Verdict.BAD_SIGNATURE;
        }
        SignedParameters.Verdict verdict = SignedParameters.verify(keys.fetchKey(tenant),
                respondentContext(tenant, assetId, versionNo, fingerprint(tenant, respondentToken)),
                presented, now);
        return translate(verdict);
    }

    /**
     * 参与者令牌的租户内指纹（小写十六进制，64 位）。不可逆，且按租户派生密钥：
     * 两个租户的同一个令牌算不出同一个指纹。<b>平台库里存的就是它，不是令牌本身。</b>
     */
    public String fingerprint(TenantId tenant, String respondentToken) {
        String token = respondentToken == null ? "" : respondentToken;
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(keys.bindKey(tenant));
            return HexFormat.of().formatHex(mac.doFinal(token.getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 unavailable", e);
        }
    }

    static String path(TenantId tenant, UUID assetId, int versionNo) {
        return PATH_PREFIX + tenant.value() + "/" + assetId + "/" + versionNo;
    }

    static String respondentPath(TenantId tenant, UUID assetId, int versionNo) {
        return PATH_PREFIX + RESPONDENT_SEGMENT + "/" + tenant.value() + "/" + assetId + "/" + versionNo;
    }

    private static Verdict translate(SignedParameters.Verdict verdict) {
        return switch (verdict) {
            case VALID -> Verdict.VALID;
            case EXPIRED -> Verdict.EXPIRED;
            case MISSING_SIGNATURE, MALFORMED, BAD_SIGNATURE -> Verdict.BAD_SIGNATURE;
        };
    }

    private static String context(TenantId tenant, UUID assetId, int versionNo) {
        return "asset/v1/" + tenant.value() + "/" + assetId + "/" + versionNo;
    }

    /** 与 bearer 的上下文<b>刻意不同</b>：一张票不能当另一张用，两个方向都挡。 */
    private static String respondentContext(TenantId tenant, UUID assetId, int versionNo, String fingerprint) {
        return "asset/v1/respondent/" + tenant.value() + "/" + assetId + "/" + versionNo + "/" + fingerprint;
    }
}
