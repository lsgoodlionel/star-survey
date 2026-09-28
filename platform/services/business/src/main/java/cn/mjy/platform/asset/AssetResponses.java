package cn.mjy.platform.asset;

import java.time.Duration;
import java.util.UUID;
import org.slf4j.Logger;
import org.springframework.core.io.InputStreamResource;
import org.springframework.http.CacheControl;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;

/**
 * 两条匿名取件端点（bearer 的 {@code /a/**} 与绑定作答者的 {@code /a/r/**}）共用的应答形状。
 *
 * <p><b>共用一份是安全要求，不是去重洁癖</b>：ADR 0019 决定 4 要求"验签及其之前的一切失败返回
 * 逐字节相同的 404"。两条路各写一份，早晚会有一处漏掉 {@code Cache-Control} 或改了一个字，
 * 那一刻它就成了区分器——而区分器就是预言机（ADR 0014 飞书回调那次的教训）。
 */
final class AssetResponses {

    /** 一切拒绝共用这一段应答体，逐字节相同。 */
    static final String NOT_FOUND_BODY = "{\"error\":\"not_found\"}";
    static final String GONE_BODY = "{\"error\":\"gone\"}";
    /** 纵深防御：即便将来白名单被放宽错了，浏览器也不会把它当文档执行。 */
    static final String SANDBOX_CSP = "default-src 'none'; sandbox";
    /** (资产, 版本) 的字节是不可变的，让浏览器缓存是对的；private 挡住共享缓存与 CDN。 */
    private static final long CACHE_SECONDS = 3600;

    private AssetResponses() {
    }

    /** 统一拒绝。原因只进服务端日志——调用方拿到的永远是同一段体。 */
    static ResponseEntity<String> notFound(Logger log, String reason) {
        log.debug("asset fetch refused: {}", reason);
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .contentType(MediaType.APPLICATION_JSON)
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(NOT_FOUND_BODY);
    }

    /** 签名正确但已过期：走到这一步的人本来就持有过合法链接，说"过期了"不泄漏新东西。 */
    static ResponseEntity<String> gone() {
        return ResponseEntity.status(HttpStatus.GONE)
                .contentType(MediaType.APPLICATION_JSON)
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(GONE_BODY);
    }

    static ResponseEntity<InputStreamResource> serve(UUID assetId, int versionNo, AssetContent content) {
        ContentDisposition disposition = ContentDisposition.inline()
                .filename(assetId + "-v" + versionNo + extensionOf(content.contentType()))
                .build();
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(content.contentType()))
                .contentLength(content.byteSize())
                .cacheControl(CacheControl.maxAge(Duration.ofSeconds(CACHE_SECONDS)).cachePrivate())
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
