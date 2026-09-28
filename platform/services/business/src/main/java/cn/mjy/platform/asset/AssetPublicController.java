package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
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
 * 匿名取件：{@code GET /a/{租户}/{资产}/{版本}?exp=…&sig=…}（ADR 0019 决定 4）。
 *
 * <p><b>验签之前零数据库 IO。</b>路径解析与 HMAC 都是纯进程内计算；验签通过之后才带着票据里
 * 那个（已签名的）租户标识进 {@code TenantScope} 读元数据。租户是签名的一部分，
 * 因此不需要"先认出租户"的控制平面表。
 *
 * <p><b>拒绝口径</b>：验签及其之前的一切失败——路径形状不对、缺参数、多带参数、签名不匹配、
 * 主密钥未配置——以及之后的"资产或版本不存在"，一律返回<b>逐字节相同</b>的 404。
 * 唯一的例外是签名正确但已过期：410，因为走到这一步的人本来就持有过合法链接。
 * 原因只进服务端日志（ADR 0018 决定 5 的教训）。
 *
 * <p>本端点是 <b>bearer</b>：拿到链接的人能看到那张图，这与问卷媒体的语义相符。
 * 作答者自己上传的内容<b>不走这条路</b>——它在 {@link AssetRespondentController} 上，
 * 取件要同时出示那位作答者的令牌（决定 9）。这里读不到作答者资产，有用例钉着。
 */
@RestController
@RequestMapping("/a")
class AssetPublicController {

    private static final Logger log = LoggerFactory.getLogger(AssetPublicController.class);

    private final AssetTickets tickets;
    private final AssetService assets;

    AssetPublicController(AssetTickets tickets, AssetService assets) {
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
        AssetTickets.Verdict verdict = tickets.verify(target.tenant(), target.assetId(), target.versionNo(),
                query, Instant.now());
        if (verdict == AssetTickets.Verdict.EXPIRED) {
            log.info("asset ticket expired for {} v{}", target.assetId(), target.versionNo());
            return AssetResponses.gone();
        }
        if (verdict != AssetTickets.Verdict.VALID) {
            return AssetResponses.notFound(log, "bad ticket");
        }
        try {
            return AssetResponses.serve(target.assetId(), target.versionNo(),
                    assets.open(target.tenant(), target.assetId(), target.versionNo()));
        } catch (AssetNotFoundException e) {
            return AssetResponses.notFound(log, "no such asset version");
        }
    }

    private record Target(TenantId tenant, UUID assetId, int versionNo) {
    }

    /** 路径形状不对时返回 null，由调用方给出与"不存在"一模一样的应答。 */
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
