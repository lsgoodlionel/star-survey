package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.SignedParameters;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 按作答者身份授权的取件：
 * {@code GET /a/r/{租户}/{资产}/{版本}?exp=…&sig=…&rt=<参与者令牌>}（ADR 0019 决定 9）。
 *
 * <p>与 {@link AssetPublicController} 的分工：那条是 <b>bearer</b>（问卷媒体，发给每个作答者看的），
 * 这条是<b>绑定</b>（作答者自己上传的录音、录像、画布快照）。两条路互不通用，
 * 一张票拿到另一条路上去一律 404。
 *
 * <p><b>{@code rt} 在验签之前先摘出来</b>，不进签名参数集合，而是折进签名上下文的指纹里。
 * 这是本决定的关键一步：若把它当普通签名参数，令牌就写死在链接里，票据又退回 bearer。
 *
 * <p>其余口径与决定 4 完全一致：验签之前零数据库 IO；一切失败返回<b>逐字节相同</b>的 404；
 * 唯一的例外是签名正确但已过期的 410。
 */
@RestController
@RequestMapping("/a/r")
class AssetRespondentController {

    private static final Logger log = LoggerFactory.getLogger(AssetRespondentController.class);

    private final AssetTickets tickets;
    private final AssetService assets;

    AssetRespondentController(AssetTickets tickets, AssetService assets) {
        this.tickets = tickets;
        this.assets = assets;
    }

    @GetMapping("/{tenant}/{asset}/{version}")
    ResponseEntity<?> fetch(@PathVariable String tenant, @PathVariable String asset, @PathVariable String version,
            @RequestParam Map<String, String> query) {
        Target target = parse(tenant, asset, version);
        if (target == null) {
            return AssetResponses.notFound(log, "malformed path");
        }
        // rt 先摘出来：它是身份凭据，不是签名参数。
        Map<String, String> signedOnly = new LinkedHashMap<>(query);
        String respondentToken = signedOnly.remove(SignedParameters.RESPONDENT_PARAM);
        if (respondentToken == null || respondentToken.isBlank()) {
            // 链接本身不足以取件——这正是与 bearer 票的分界。
            return AssetResponses.notFound(log, "no respondent token presented");
        }
        AssetTickets.Verdict verdict = tickets.verifyForRespondent(target.tenant(), target.assetId(),
                target.versionNo(), respondentToken, signedOnly, Instant.now());
        if (verdict == AssetTickets.Verdict.EXPIRED) {
            log.info("respondent asset ticket expired for {} v{}", target.assetId(), target.versionNo());
            return AssetResponses.gone();
        }
        if (verdict != AssetTickets.Verdict.VALID) {
            return AssetResponses.notFound(log, "bad bound ticket");
        }
        try {
            // 第二道闸：签名那道已经够，这道挡的是「签发逻辑将来被改坏」。两道互不依赖。
            String fingerprint = tickets.fingerprint(target.tenant(), respondentToken);
            AssetContent content = assets.openForRespondent(target.tenant(), target.assetId(),
                    target.versionNo(), fingerprint);
            return AssetResponses.serve(target.assetId(), target.versionNo(), content);
        } catch (AssetNotFoundException e) {
            return AssetResponses.notFound(log, "no such respondent asset version");
        }
    }

    private record Target(TenantId tenant, UUID assetId, int versionNo) {
    }

    private static Target parse(String tenant, String asset, String version) {
        try {
            int versionNo = Integer.parseInt(version);
            if (versionNo < 1) {
                return null;
            }
            return new Target(new TenantId(UUID.fromString(tenant)), UUID.fromString(asset), versionNo);
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
