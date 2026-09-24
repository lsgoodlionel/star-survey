package cn.mjy.platform.response;

import cn.mjy.platform.response.AnswerBatch.ExtensionAnswer;
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
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
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
 * 契约 response-read-v1 的 HTTP 实现：{@code POST <网关>/v1/responses/read}。
 *
 * <p>与发布共用网关地址与共享密钥（{@code PLATFORM_PUBGW_URL} / {@code PLATFORM_PUBGW_SECRET}），签名规则相同。
 * 未配置时不阻止启动，但每次读取都失败（503），不会返回"没有作答"的假象。应答里出现未请求的答卷号即视为不可信。
 * 作答值是个人数据：本类只记录状态码与原因，从不记录请求或应答正文。
 */
@Component
public class HttpResponseAnswerSource implements ResponseAnswerSource, RespondentSource {

    static final String READ_PATH = "/v1/responses/read";
    /** 共享密钥的最短长度。注册回读实现的条件也用这一个常量，避免两处阈值分叉。 */
    public static final int MIN_SECRET_BYTES = 32;

    private static final Logger log = LoggerFactory.getLogger(HttpResponseAnswerSource.class);

    private final URI readUri;
    private final byte[] secret;
    private final Duration timeout;
    private final Clock clock;
    private final JsonMapper json;
    private final HttpClient http;

    @Autowired
    public HttpResponseAnswerSource(@Value("${platform.pubgw.url:}") String url,
            @Value("${platform.pubgw.secret:}") String secret,
            @Value("${platform.pubgw.read-timeout-seconds:30}") long timeoutSeconds,
            JsonMapper json) {
        this(parseBase(url), secret, Duration.ofSeconds(timeoutSeconds), Clock.systemUTC(), json);
    }

    HttpResponseAnswerSource(URI baseUrl, String secret, Duration timeout, Clock clock, JsonMapper json) {
        this.readUri = baseUrl == null ? null : baseUrl.resolve(READ_PATH);
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        this.timeout = timeout;
        this.clock = clock;
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    public boolean isConfigured() {
        return readUri != null && secret.length >= MIN_SECRET_BYTES;
    }

    @Override
    public AnswerBatch read(AnswerQuery query) {
        if (query.responseIds().isEmpty()) {
            return AnswerBatch.empty();
        }
        if (!isConfigured()) {
            throw new ResponseAnswersUnavailableException("publish gateway is not configured");
        }
        HttpResponse<byte[]> response = send(body(query));
        if (response.statusCode() != 200) {
            log.warn("response read on {} sid {} refused with http {}", query.engineInstanceId(), query.engineSid(),
                    response.statusCode());
            throw new ResponseAnswersUnavailableException("gateway returned http " + response.statusCode());
        }
        return parse(response.body(), query);
    }

    private byte[] body(AnswerQuery query) {
        ObjectNode envelope = json.createObjectNode();
        envelope.put("engineInstanceId", query.engineInstanceId());
        envelope.put("surveyId", query.engineSid());
        query.responseIds().forEach(envelope.putArray("responseIds")::add);
        query.fieldnames().forEach(envelope.putArray("fields")::add);
        if (query.wantsExtensions()) {
            envelope.put("generation", query.generation());
            query.extensionQuestions().forEach(envelope.putArray("extensionQuestions")::add);
        }
        return json.writeValueAsBytes(envelope);
    }

    private byte[] body(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames,
            boolean includeRespondent) {
        ObjectNode envelope = json.createObjectNode();
        envelope.put("engineInstanceId", engineInstanceId);
        envelope.put("surveyId", engineSid);
        responseIds.forEach(envelope.putArray("responseIds")::add);
        fieldnames.forEach(envelope.putArray("fields")::add);
        if (includeRespondent) {
            envelope.put("includeRespondent", true);
        }
        return json.writeValueAsBytes(envelope);
    }

    /**
     * 读取"每份答卷是用哪个邀请码答的"（契约 response-read-v1「参与者令牌」，ADR 0016 缺口 (b)）。
     *
     * <p>只取令牌、不取任何作答列（{@code fields} 传空）。<b>认不出是谁的答卷不出现在结果里</b>：
     * 匿名问卷网关一律回 {@code null}，没用邀请码进场的答卷也是——都不能当作某个人答的。
     * 令牌不落库，每轮对账现读现用。
     *
     * @return 答卷号 → 参与者令牌，只含确实认得出的那些
     * @throws ResponseAnswersUnavailableException 网关未配置、不可达、拒绝或应答不可信
     */
    @Override
    public Map<Long, String> readRespondents(String engineInstanceId, long engineSid, List<Long> responseIds) {
        if (responseIds.isEmpty()) {
            return Map.of();
        }
        if (!isConfigured()) {
            throw new ResponseAnswersUnavailableException("publish gateway is not configured");
        }
        byte[] body = body(engineInstanceId, engineSid, responseIds, List.of(), true);
        HttpResponse<byte[]> response = send(body);
        if (response.statusCode() != 200) {
            log.warn("respondent read on {} sid {} refused with http {}", engineInstanceId, engineSid,
                    response.statusCode());
            throw new ResponseAnswersUnavailableException("gateway returned http " + response.statusCode());
        }
        return parseRespondents(response.body(), Set.copyOf(responseIds));
    }

    private Map<Long, String> parseRespondents(byte[] body, Set<Long> requested) {
        try {
            JsonNode responses = json.readTree(body).get("responses");
            if (responses == null || !responses.isArray()) {
                throw untrusted("reply has no responses array");
            }
            Map<Long, String> tokens = new LinkedHashMap<>();
            Set<Long> seen = new HashSet<>();
            for (JsonNode entry : responses) {
                long id = responseId(entry, requested, seen);
                JsonNode token = entry.get("token");
                if (token == null) {
                    // 要了令牌却没这个键：网关没按契约办事，不能当作"认不出"。
                    throw untrusted("reply entry carries no token key");
                }
                if (token.isString() && !token.asString().isBlank()) {
                    tokens.put(id, token.asString());
                }
            }
            return Map.copyOf(tokens);
        } catch (JacksonException e) {
            throw untrusted("reply is not JSON");
        }
    }

    private HttpResponse<byte[]> send(byte[] body) {
        String timestamp = Long.toString(clock.instant().getEpochSecond());
        HttpRequest request = HttpRequest.newBuilder(readUri)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-Pubgw-Timestamp", timestamp)
                .header("X-Pubgw-Signature", PublishGatewaySigner.sign(secret, timestamp, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (HttpTimeoutException e) {
            throw new ResponseAnswersUnavailableException("gateway timed out after " + timeout);
        } catch (IOException e) {
            throw new ResponseAnswersUnavailableException("gateway network error: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ResponseAnswersUnavailableException("interrupted while reading answers");
        }
    }

    private AnswerBatch parse(byte[] body, AnswerQuery query) {
        Set<Long> requested = Set.copyOf(query.responseIds());
        try {
            JsonNode reply = json.readTree(body);
            JsonNode responses = reply.get("responses");
            if (responses == null || !responses.isArray()) {
                throw untrusted("reply has no responses array");
            }
            Map<Long, Map<String, String>> answers = new LinkedHashMap<>();
            Set<Long> seen = new HashSet<>();
            for (JsonNode entry : responses) {
                long id = responseId(entry, requested, seen);
                answers.put(id, values(entry.get("values"), query.fieldnames()));
            }
            return new AnswerBatch(answers, extensions(reply, query, requested));
        } catch (JacksonException e) {
            throw untrusted("reply is not JSON");
        }
    }

    /**
     * 扩展副表作答（契约 response-read-v1「扩展表作答」）：<b>失败即关闭</b>——点了名却没有这一段、
     * 出现没点名的答卷或题目、形状不对，一律视为应答不可信，绝不当成「这批没有副表作答」。
     */
    private static Map<Long, Map<String, ExtensionAnswer>> extensions(JsonNode reply, AnswerQuery query,
            Set<Long> requested) {
        if (!query.wantsExtensions()) {
            return Map.of();
        }
        JsonNode section = reply.get("extensionAnswers");
        if (section == null || !section.isObject()) {
            throw untrusted("reply carries no extensionAnswers object");
        }
        Set<String> questions = Set.copyOf(query.extensionQuestions());
        Map<Long, Map<String, ExtensionAnswer>> found = new LinkedHashMap<>();
        for (Map.Entry<String, JsonNode> entry : section.properties()) {
            if (!entry.getValue().isObject()) {
                throw untrusted("extensionAnswers entry is not an object");
            }
            Map<String, ExtensionAnswer> byQuestion = new LinkedHashMap<>();
            for (Map.Entry<String, JsonNode> question : entry.getValue().properties()) {
                if (!questions.contains(question.getKey())) {
                    throw untrusted("extensionAnswers carries a question that was not requested");
                }
                byQuestion.put(question.getKey(), extensionAnswer(question.getValue()));
            }
            found.put(extensionResponseId(entry.getKey(), requested), byQuestion);
        }
        return found;
    }

    private static long extensionResponseId(String key, Set<Long> requested) {
        try {
            long id = Long.parseLong(key);
            if (!requested.contains(id)) {
                throw untrusted("extensionAnswers carries a response id that was not requested");
            }
            return id;
        } catch (NumberFormatException e) {
            throw untrusted("extensionAnswers key is not a response id");
        }
    }

    private static ExtensionAnswer extensionAnswer(JsonNode node) {
        JsonNode version = node.get("structureVersion");
        JsonNode valid = node.get("isValid");
        JsonNode rows = node.get("rows");
        if (version == null || !version.isString() || valid == null || !valid.isBoolean()
                || rows == null || !rows.isArray()) {
            throw untrusted("extensionAnswers entry is not a side-table answer");
        }
        List<Map<String, String>> parsed = new ArrayList<>();
        for (JsonNode row : rows) {
            if (!row.isObject()) {
                throw untrusted("a side-table row is not an object");
            }
            Map<String, String> cells = new LinkedHashMap<>();
            for (Map.Entry<String, JsonNode> cell : row.properties()) {
                JsonNode value = cell.getValue();
                cells.put(cell.getKey(), value.isString() ? value.asString() : value.toString());
            }
            parsed.add(cells);
        }
        return new ExtensionAnswer(version.asString(), valid.asBoolean(), List.copyOf(parsed));
    }

    private static long responseId(JsonNode entry, Set<Long> requested, Set<Long> seen) {
        JsonNode id = entry == null ? null : entry.get("id");
        if (id == null || !id.isIntegralNumber() || !requested.contains(id.asLong()) || !seen.add(id.asLong())) {
            throw untrusted("reply contains an unrequested or duplicate response id");
        }
        return id.asLong();
    }

    /** 只保留请求的列；缺的列当作未作答（null）。 */
    private static Map<String, String> values(JsonNode values, List<String> fieldnames) {
        if (values == null || !values.isObject()) {
            throw untrusted("reply entry has no values object");
        }
        Map<String, String> projected = new LinkedHashMap<>();
        for (String field : fieldnames) {
            JsonNode value = values.get(field);
            projected.put(field, value == null || value.isNull() ? null
                    : value.isString() ? value.asString() : value.toString());
        }
        return projected;
    }

    private static ResponseAnswersUnavailableException untrusted(String reason) {
        log.error("response read reply rejected: {}", reason);
        return new ResponseAnswersUnavailableException("untrusted gateway reply: " + reason);
    }

    private static URI parseBase(String url) {
        if (url == null || url.isBlank()) {
            return null;
        }
        try {
            URI uri = URI.create(url.trim());
            return uri.getScheme() != null && uri.getScheme().startsWith("http") ? uri : null;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
