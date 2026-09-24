package cn.mjy.platform.asset;

import cn.mjy.platform.survey.gateway.PublishGatewaySigner;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * {@code POST <网关>/v1/responses/uploads}：把作答者上传从插件拉到平台（ADR 0019 决定 8）。
 *
 * <p>与 {@code HttpResponseAnswerSource} 共用网关地址、共享密钥与签名规则。
 * 网关的应答是<b>外部数据</b>：出现没请求过的答卷号或 token、声明大小与字节对不上、
 * 不是 JSON，一律视为不可信而拒绝——<b>绝不降级成"这份答卷没有上传"</b>。
 *
 * <p>上传内容是个人数据：本类只记实例、sid、条数与失败原因，从不记文件名、不记字节。
 */
@Component
public class HttpResponderUploadSource implements ResponderUploadSource {

    static final String UPLOADS_PATH = "/v1/responses/uploads";
    /** 共享密钥的最短长度，与作答读取同一个阈值。 */
    public static final int MIN_SECRET_BYTES = 32;
    /** 单件上传的字节上限；插件、网关、平台三处各判各的。 */
    static final long MAX_CONTENT_BYTES = 16L * 1024 * 1024;

    private static final Logger log = LoggerFactory.getLogger(HttpResponderUploadSource.class);

    private final URI uploadsUri;
    private final byte[] secret;
    private final Duration timeout;
    private final Clock clock;
    private final JsonMapper json;
    private final HttpClient http;

    @Autowired
    public HttpResponderUploadSource(@Value("${platform.pubgw.url:}") String url,
            @Value("${platform.pubgw.secret:}") String secret,
            @Value("${platform.pubgw.read-timeout-seconds:60}") long timeoutSeconds,
            JsonMapper json) {
        this(parseBase(url), secret, Duration.ofSeconds(timeoutSeconds), Clock.systemUTC(), json);
    }

    HttpResponderUploadSource(URI baseUrl, String secret, Duration timeout, Clock clock, JsonMapper json) {
        this.uploadsUri = baseUrl == null ? null : baseUrl.resolve(UPLOADS_PATH);
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        this.timeout = timeout;
        this.clock = clock;
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    public boolean isConfigured() {
        return uploadsUri != null && secret.length >= MIN_SECRET_BYTES;
    }

    /** 一页答卷的上传清单。空页不出网。 */
    @Override
    public List<ResponderUploadRef> manifest(String engineInstanceId, long engineSid, String generation,
            List<Long> responseIds) {
        if (responseIds.isEmpty()) {
            return List.of();
        }
        ObjectNode envelope = envelope(engineInstanceId, engineSid, generation);
        responseIds.forEach(envelope.putArray("responseIds")::add);
        JsonNode reply = call(engineInstanceId, engineSid, envelope);
        return parseManifest(reply, Set.copyOf(responseIds));
    }

    /** 一件上传的字节。一次只取一件，应答体量因此有界。 */
    @Override
    public ResponderUploadBytes content(String engineInstanceId, long engineSid, String generation,
            UUID uploadToken) {
        ObjectNode envelope = envelope(engineInstanceId, engineSid, generation);
        envelope.put("uploadToken", uploadToken.toString());
        return parseContent(call(engineInstanceId, engineSid, envelope), uploadToken);
    }

    private ObjectNode envelope(String engineInstanceId, long engineSid, String generation) {
        ObjectNode envelope = json.createObjectNode();
        envelope.put("engineInstanceId", engineInstanceId);
        envelope.put("surveyId", engineSid);
        // 代次是上传会话自然键的一段：没有它就可能串代次，所以永远随行。
        envelope.put("generation", generation);
        return envelope;
    }

    private JsonNode call(String engineInstanceId, long engineSid, ObjectNode envelope) {
        if (!isConfigured()) {
            throw unavailable("publish gateway is not configured");
        }
        byte[] body = json.writeValueAsBytes(envelope);
        HttpResponse<byte[]> response = send(body);
        if (response.statusCode() != 200) {
            log.warn("upload read on {} sid {} refused with http {}", engineInstanceId, engineSid,
                    response.statusCode());
            throw unavailable("gateway returned http " + response.statusCode());
        }
        try {
            return json.readTree(response.body());
        } catch (JacksonException e) {
            throw unavailable("gateway reply is not JSON");
        }
    }

    private List<ResponderUploadRef> parseManifest(JsonNode reply, Set<Long> requested) {
        JsonNode uploads = reply.get("uploads");
        if (uploads == null || !uploads.isObject()) {
            throw unavailable("gateway reply has no uploads object");
        }
        List<ResponderUploadRef> refs = new ArrayList<>();
        Set<Long> seen = new HashSet<>();
        for (String key : uploads.propertyNames()) {
            long responseId = responseId(key, requested, seen);
            JsonNode entries = uploads.get(key);
            if (!entries.isArray()) {
                throw unavailable("gateway reply lists uploads that are not an array");
            }
            for (JsonNode entry : entries) {
                refs.add(ref(entry, responseId));
            }
        }
        return List.copyOf(refs);
    }

    private ResponderUploadRef ref(JsonNode entry, long responseId) {
        if (entry == null || !entry.isObject()) {
            throw unavailable("an upload entry is not an object");
        }
        UUID token = uuid(entry.get("uploadToken"));
        String code = text(entry.get("questionCode"));
        if (code.isBlank()) {
            throw unavailable("an upload entry has no question code");
        }
        long size = entry.path("sizeBytes").asLong(-1);
        if (size < 0) {
            throw unavailable("an upload entry has no usable size");
        }
        return new ResponderUploadRef(token, responseId, code, text(entry.get("originalName")),
                text(entry.get("extension")), size);
    }

    private ResponderUploadBytes parseContent(JsonNode reply, UUID wanted) {
        UUID token = uuid(reply.get("uploadToken"));
        if (!token.equals(wanted)) {
            // 网关回的是另一件上传：绝不能当成这一件存下去。
            throw unavailable("gateway answered about an upload that was not asked for");
        }
        long responseId = reply.path("responseId").asLong(-1);
        if (responseId < 0) {
            throw unavailable("the upload has no usable response id");
        }
        long declared = reply.path("sizeBytes").asLong(-1);
        if (declared < 0 || declared > MAX_CONTENT_BYTES) {
            throw unavailable("the upload has no usable size");
        }
        String encoded = text(reply.get("contentBase64"));
        byte[] content;
        try {
            content = Base64.getDecoder().decode(encoded);
        } catch (IllegalArgumentException e) {
            throw unavailable("the upload content is not base64");
        }
        if (content.length != declared) {
            // 声明与实际对不上就拒绝：截断过的字节存成一件资产而没人察觉，比报错危险得多。
            throw unavailable("the upload's declared size disagrees with its bytes");
        }
        String code = text(reply.get("questionCode"));
        if (code.isBlank()) {
            throw unavailable("the upload has no question code");
        }
        ResponderUploadRef ref = new ResponderUploadRef(token, responseId, code, text(reply.get("originalName")),
                text(reply.get("extension")), content.length);
        return new ResponderUploadBytes(ref, content);
    }

    private long responseId(String key, Set<Long> requested, Set<Long> seen) {
        long id;
        try {
            id = Long.parseLong(key);
        } catch (NumberFormatException e) {
            throw unavailable("gateway reply keys uploads by something that is not a response id");
        }
        if (!requested.contains(id) || !seen.add(id)) {
            throw unavailable("gateway reply names a response that was not asked for");
        }
        return id;
    }

    private UUID uuid(JsonNode node) {
        if (node == null || !node.isString()) {
            throw unavailable("an upload token is missing");
        }
        try {
            return UUID.fromString(node.asString());
        } catch (IllegalArgumentException e) {
            throw unavailable("an upload token is not a UUID");
        }
    }

    private static String text(JsonNode node) {
        return node != null && node.isString() ? node.asString() : "";
    }

    private HttpResponse<byte[]> send(byte[] body) {
        String timestamp = Long.toString(clock.instant().getEpochSecond());
        HttpRequest request = HttpRequest.newBuilder(uploadsUri)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-Pubgw-Timestamp", timestamp)
                .header("X-Pubgw-Signature", PublishGatewaySigner.sign(secret, timestamp, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (HttpTimeoutException e) {
            throw unavailable("gateway timed out after " + timeout);
        } catch (IOException e) {
            throw unavailable("gateway network error: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw unavailable("interrupted while reading uploads");
        }
    }

    private static ResponderUploadsUnavailableException unavailable(String reason) {
        return new ResponderUploadsUnavailableException(reason);
    }

    private static URI parseBase(String url) {
        if (url == null || url.isBlank()) {
            return null;
        }
        try {
            return URI.create(url.replaceAll("/+$", "") + "/");
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
