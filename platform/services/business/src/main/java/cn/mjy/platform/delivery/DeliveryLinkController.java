package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.DeliveryLinkService.Embed;
import cn.mjy.platform.delivery.DeliveryLinkService.NewLink;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 租户端：问卷的投放链接、二维码与内嵌代码。租户只取自已验签令牌。 */
@RestController
@RequestMapping("/v1/delivery")
public class DeliveryLinkController {

    private static final MediaType PNG = MediaType.IMAGE_PNG;
    private static final MediaType SVG = MediaType.valueOf("image/svg+xml");
    /** 二维码图片可以放心缓存：内容只随链接本身变化，而链接一旦建好就不再改。 */
    private static final String QR_CACHE_CONTROL = "private, max-age=3600";

    private final DeliveryLinkService links;
    private final CurrentTenant currentTenant;

    DeliveryLinkController(DeliveryLinkService links, CurrentTenant currentTenant) {
        this.links = links;
        this.currentTenant = currentTenant;
    }

    /** 建链接。params 是业务参数，平台不解释含义，只保证签名后不可篡改。 */
    public record CreateLink(
            @NotBlank @Size(max = 64) String label,
            Map<String, String> params,
            @Min(60) @Max(31_536_000) Long ttlSeconds,
            boolean shortLink) {
    }

    @PostMapping("/surveys/{surveyId}/links")
    ResponseEntity<LinkView> create(@PathVariable UUID surveyId, @Valid @RequestBody CreateLink request) {
        TenantContext ctx = currentTenant.require();
        Duration ttl = request.ttlSeconds() == null ? null : Duration.ofSeconds(request.ttlSeconds());
        LinkView link = links.create(ctx, surveyId,
                new NewLink(request.label(), request.params(), ttl, request.shortLink()));
        return ResponseEntity.status(201).body(link);
    }

    @GetMapping("/surveys/{surveyId}/links")
    List<LinkView> list(@PathVariable UUID surveyId) {
        return links.list(currentTenant.require(), surveyId);
    }

    @GetMapping("/links/{linkId}")
    LinkView get(@PathVariable UUID linkId) {
        return links.find(currentTenant.require(), linkId);
    }

    @DeleteMapping("/links/{linkId}")
    LinkView revoke(@PathVariable UUID linkId) {
        return links.revoke(currentTenant.require(), linkId);
    }

    @GetMapping("/links/{linkId}/qr")
    ResponseEntity<byte[]> qr(@PathVariable UUID linkId,
            @RequestParam(defaultValue = "png") String format,
            @RequestParam(defaultValue = "6") @Min(1) @Max(40) int moduleSize) {
        QrCode qr = links.qrCode(currentTenant.require(), linkId);
        boolean svg = "svg".equalsIgnoreCase(format);
        if (!svg && !"png".equalsIgnoreCase(format)) {
            throw DeliveryExceptions.invalid("format must be png or svg");
        }
        byte[] body = svg
                ? QrCodeRenderer.svg(qr, moduleSize, QrCodeRenderer.DEFAULT_QUIET_ZONE)
                        .getBytes(StandardCharsets.UTF_8)
                : QrCodeRenderer.png(qr, moduleSize, QrCodeRenderer.DEFAULT_QUIET_ZONE);
        return ResponseEntity.ok()
                .contentType(svg ? SVG : PNG)
                .header(HttpHeaders.CACHE_CONTROL, QR_CACHE_CONTROL)
                .body(body);
    }

    @GetMapping("/links/{linkId}/embed")
    Embed embed(@PathVariable UUID linkId,
            @RequestParam(defaultValue = "640") int width,
            @RequestParam(defaultValue = "800") int height) {
        return links.embed(currentTenant.require(), linkId, width, height);
    }
}
