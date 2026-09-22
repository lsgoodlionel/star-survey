package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.api.NotFoundException;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.io.InputStream;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Collections;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * 通讯录事件回调的匿名端点（独立过滤链，见 {@link OrgEventSecurityConfig}）：
 * {@code GET|POST /v1/org-events/{tenantId}/{connectionId}}。开放平台由连接决定。
 *
 * <p>请求体先按上限读取（超限 413，不解析）；查询串自行解析，不让容器按表单解析请求体。
 * 路径里的租户只用来限定数据库作用域；真正的认证是各开放平台的签名 / 加密（见各 {@link OrgEventReceiver}）。
 */
@RestController
@RequestMapping(OrgEventService.EVENT_PATH)
class OrgEventController {

    private final OrgEventService events;

    OrgEventController(OrgEventService events) {
        this.events = events;
    }

    @RequestMapping(path = "/{tenantId}/{connectionId}", method = {RequestMethod.GET, RequestMethod.POST})
    ResponseEntity<String> receive(@PathVariable String tenantId, @PathVariable String connectionId,
            HttpServletRequest request) throws IOException {
        OrgEventReceiver.Request event = new OrgEventReceiver.Request(request.getMethod(),
                query(request.getQueryString()), headers(request), body(request), Instant.now());
        OrgEventReceiver.Reply reply = events.receive(tenant(tenantId), connection(connectionId), event);
        return ResponseEntity.ok().contentType(reply.contentType()).cacheControl(CacheControl.noStore())
                .body(reply.body());
    }

    private byte[] body(HttpServletRequest request) throws IOException {
        int max = events.maxBodyBytes();
        if (request.getContentLengthLong() > max) {
            throw OrgEventException.tooLarge();
        }
        try (InputStream in = request.getInputStream()) {
            byte[] body = in.readNBytes(max + 1);
            if (body.length > max) {
                throw OrgEventException.tooLarge();
            }
            return body;
        }
    }

    private static Map<String, String> query(String raw) {
        if (raw == null || raw.isEmpty()) {
            return Map.of();
        }
        Map<String, String> params = new HashMap<>();
        for (String pair : raw.split("&")) {
            String[] kv = pair.split("=", 2);
            try {
                String name = URLDecoder.decode(kv[0], StandardCharsets.UTF_8);
                String value = kv.length > 1 ? URLDecoder.decode(kv[1], StandardCharsets.UTF_8) : "";
                params.putIfAbsent(name, value);
            } catch (IllegalArgumentException e) {
                throw OrgEventException.malformed("query string is malformed");
            }
        }
        return Map.copyOf(params);
    }

    private static Map<String, String> headers(HttpServletRequest request) {
        Map<String, String> headers = new HashMap<>();
        for (String name : Collections.list(request.getHeaderNames())) {
            headers.put(name.toLowerCase(Locale.ROOT), request.getHeader(name));
        }
        return Map.copyOf(headers);
    }

    private static TenantId tenant(String value) {
        try {
            return TenantId.of(value);
        } catch (IllegalArgumentException e) {
            throw new NotFoundException("organisation connection not found");
        }
    }

    private static UUID connection(String value) {
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException e) {
            throw new NotFoundException("organisation connection not found");
        }
    }
}
