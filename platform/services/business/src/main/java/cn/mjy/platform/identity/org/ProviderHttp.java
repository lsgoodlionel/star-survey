package cn.mjy.platform.identity.org;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 调开放平台的 HTTP 工具（JDK HttpClient + JSON）。网络错误与不可解析的应答统一变成
 * {@link OrgProviderException.Kind#UNAVAILABLE}；异常消息只带提供方与路径——查询串里有 access_token、
 * corpsecret，绝不能出现在消息或日志里。
 */
@Component
class ProviderHttp {

    record Response(int status, JsonNode body) {

        boolean ok() {
            return status == 200;
        }

        String text(String field) {
            JsonNode value = body.get(field);
            return value == null || value.isNull() ? null : value.asString();
        }

        int intValue(String field, int fallback) {
            JsonNode value = body.get(field);
            return value == null || !value.isNumber() ? fallback : value.asInt();
        }
    }

    private final HttpClient http;
    private final JsonMapper json;
    private final OrgLoginProperties properties;

    ProviderHttp(JsonMapper json, OrgLoginProperties properties) {
        this.json = json;
        this.properties = properties;
        this.http = HttpClient.newBuilder()
                .connectTimeout(properties.httpTimeout())
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    Response get(OrgProvider provider, String base, String path, Map<String, String> query,
            Map<String, String> headers) {
        return send(provider, path, request(base, path, query, headers).GET().build());
    }

    Response postJson(OrgProvider provider, String base, String path, Map<String, String> query,
            Map<String, String> headers, Object body) {
        HttpRequest request = request(base, path, query, headers)
                .header("Content-Type", "application/json; charset=utf-8")
                .POST(HttpRequest.BodyPublishers.ofByteArray(json.writeValueAsBytes(body)))
                .build();
        return send(provider, path, request);
    }

    static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    static String queryString(Map<String, String> query) {
        return query.entrySet().stream()
                .map(e -> encode(e.getKey()) + "=" + encode(e.getValue()))
                .collect(Collectors.joining("&"));
    }

    private HttpRequest.Builder request(String base, String path, Map<String, String> query,
            Map<String, String> headers) {
        String suffix = query.isEmpty() ? "" : "?" + queryString(query);
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(base + path + suffix))
                .timeout(properties.httpTimeout())
                .header("Accept", "application/json");
        headers.forEach(builder::header);
        return builder;
    }

    private Response send(OrgProvider provider, String path, HttpRequest request) {
        try {
            HttpResponse<byte[]> response = http.send(request, HttpResponse.BodyHandlers.ofByteArray());
            byte[] body = response.body();
            JsonNode parsed = body.length == 0 ? json.createObjectNode() : json.readTree(body);
            if (parsed == null || !parsed.isObject()) {
                throw unavailable(provider, path, "non-object response, http " + response.statusCode());
            }
            return new Response(response.statusCode(), parsed);
        } catch (JacksonException e) {
            throw unavailable(provider, path, "unreadable response");
        } catch (IOException e) {
            throw unavailable(provider, path, "network error " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw unavailable(provider, path, "interrupted");
        }
    }

    static OrgProviderException unavailable(OrgProvider provider, String path, String detail) {
        return new OrgProviderException(OrgProviderException.Kind.UNAVAILABLE,
                provider.code() + " " + path + ": " + detail);
    }
}
