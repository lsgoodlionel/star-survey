package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import jakarta.servlet.http.HttpServletRequest;
import java.net.URI;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 匿名可达的三个端点（安全链见 {@link DeliveryPublicSecurityConfig}）：短链跳转、退订、渠道商回执。
 * 都不认 Authorization 头，凭证各自内建（短链码、签名令牌、HMAC 签名）。
 */
@RestController
@RequestMapping("/d")
public class DeliveryPublicController {

    /** 退订确认页：纯文本，无脚本、无外链。 */
    private static final String CONFIRM_PAGE = """
            <!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <meta name="robots" content="noindex,nofollow"><title>退订</title></head>
            <body><h1>退订</h1><p>确认后将不再收到该组织经本渠道发送的问卷邀请与催答。</p>
            <form method="post" action="%s"><button type="submit">确认退订</button></form></body></html>
            """;

    private final DeliveryLinkService links;
    private final UnsubscribeService unsubscribes;
    private final ReceiptService receipts;

    DeliveryPublicController(DeliveryLinkService links, UnsubscribeService unsubscribes, ReceiptService receipts) {
        this.links = links;
        this.unsubscribes = unsubscribes;
        this.receipts = receipts;
    }

    /**
     * 短链跳转。不存在、已作废、已过期一律 404 且响应体完全相同——否则可以拿短链去探测哪些链接存在。
     * 跳转目标由平台按引擎登记地址拼出，调用方无法影响，因此不是开放重定向。
     */
    @GetMapping("/s/{code}")
    ResponseEntity<Void> shortLink(@PathVariable String code) {
        return ResponseEntity.status(302)
                .location(URI.create(links.resolveShortLink(code)))
                // 短链的有效期由链接本身决定，中间层不要缓存跳转结果。
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .header("Referrer-Policy", "strict-origin-when-cross-origin")
                .build();
    }

    /**
     * 退订确认页。退订这个动作放在 POST 上：邮件客户端与安全网关会替用户预取邮件里的链接，
     * GET 一点就退订会造成大量"我没点过"的误退。
     */
    @GetMapping(value = "/u/{token}", produces = MediaType.TEXT_HTML_VALUE)
    String unsubscribePage(@PathVariable String token, HttpServletRequest request) {
        return CONFIRM_PAGE.formatted(escape(request.getRequestURI()));
    }

    @PostMapping("/u/{token}")
    Map<String, Object> unsubscribe(@PathVariable String token) {
        UnsubscribeService.Result result = unsubscribes.unsubscribe(token, "unsubscribe-" + UUID.randomUUID());
        return Map.of("unsubscribed", true, "channel", result.channel(),
                "alreadyUnsubscribed", result.alreadyUnsubscribed());
    }

    /** 渠道商回执。请求体按原始字节验签，所以这里收 byte[] 而不是解析后的对象。 */
    @PostMapping("/receipts/{tenantId}/{provider}")
    ReceiptService.Result receipts(@PathVariable UUID tenantId,
            @PathVariable String provider,
            @RequestHeader(value = ReceiptService.TIMESTAMP_HEADER, required = false) String timestamp,
            @RequestHeader(value = ReceiptService.SIGNATURE_HEADER, required = false) String signature,
            @RequestBody(required = false) byte[] body) {
        return receipts.accept(new TenantId(tenantId), provider, timestamp, signature, body);
    }

    private static String escape(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }
}
