package cn.mjy.platform.asset;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.InputStreamResource;
import org.springframework.http.CacheControl;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
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
 */
@RestController
@RequestMapping("/a")
class AssetPublicController {

    /** 一切拒绝共用这一段应答体，逐字节相同。 */
    private static final String NOT_FOUND_BODY = "{\"error\":\"not_found\"}";
    private static final String GONE_BODY = "{\"error\":\"gone\"}";
    /** 纵深防御：即便将来白名单被放宽错了，浏览器也不会把它当文档执行。 */
    private static final String SANDBOX_CSP = "default-src 'none'; sandbox";
    /** (资产, 版本) 的字节是不可变的，让浏览器缓存是对的；private 挡住共享缓存与 CDN。 */
    private static final long CACHE_SECONDS = 3600;

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
            return refuse("malformed path");
        }
        AssetTickets.Verdict verdict = tickets.verify(target.tenant(), target.assetId(), target.versionNo(),
                query, Instant.now());
        if (verdict == AssetTickets.Verdict.EXPIRED) {
            log.info("asset ticket expired for {} v{}", target.assetId(), target.versionNo());
            return ResponseEntity.status(HttpStatus.GONE)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(HttpHeaders.CACHE_CONTROL, "no-store")
                    .body(GONE_BODY);
        }
        if (verdict != AssetTickets.Verdict.VALID) {
            return refuse("bad ticket");
        }
        try {
            return serve(target, assets.open(target.tenant(), target.assetId(), target.versionNo()));
        } catch (AssetNotFoundException e) {
            return refuse("no such asset version");
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

    private static ResponseEntity<String> refuse(String reason) {
        log.debug("asset fetch refused: {}", reason);
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .contentType(MediaType.APPLICATION_JSON)
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(NOT_FOUND_BODY);
    }

    private static ResponseEntity<InputStreamResource> serve(Target target, AssetContent content) {
        ContentDisposition disposition = ContentDisposition.inline()
                .filename(target.assetId() + "-v" + target.versionNo() + extensionOf(content.contentType()))
                .build();
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(content.contentType()))
                .contentLength(content.byteSize())
                .cacheControl(CacheControl.maxAge(java.time.Duration.ofSeconds(CACHE_SECONDS)).cachePrivate())
                .header("X-Content-Type-Options", "nosniff")
                .header("Content-Security-Policy", SANDBOX_CSP)
                .header(HttpHeaders.CONTENT_DISPOSITION, disposition.toString())
                .body(new InputStreamResource(content.content()));
    }

    /** 扩展名只从白名单认出来的内容类型推，永远不来自上传时的文件名。 */
    private static String extensionOf(String contentType) {
        int slash = contentType.indexOf('/');
        return slash < 0 ? "" : "." + contentType.substring(slash + 1);
    }
}
