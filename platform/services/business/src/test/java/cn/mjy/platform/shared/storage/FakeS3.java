package cn.mjy.platform.shared.storage;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * 测试用的最小 S3：PUT / GET / HEAD / DELETE / ListObjectsV2，外加一份**独立实现**的
 * SigV4 校验。
 *
 * <p>「独立」是重点：这里的规范化请求、签名密钥派生、字符串拼法全部按 AWS 的规格另写一遍，
 * 不复用被测代码的任何常量或方法。被测实现改错了签名，这里一定红；两边一起改错才会一起绿，
 * 而那要求两次独立犯同一个错。
 */
final class FakeS3 implements AutoCloseable {

    static final String BUCKET = "mjy";
    static final String REGION = "cn-test-1";
    static final String ACCESS_KEY = "AKIAEXAMPLEEXAMPLE";
    static final String SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY";

    private static final String ALGORITHM = "AWS4-HMAC-SHA256";
    private static final String SERVICE = "s3";

    /** 对象键 → 内容。 */
    private final Map<String, byte[]> objects = new ConcurrentHashMap<>();
    /** 收到过的请求（方法 ＋ 路径 ＋ 查询串），用来核对"到底发了几次、发了什么"。 */
    private final List<String> requests = new ArrayList<>();
    private final AtomicInteger refusals = new AtomicInteger();

    private final HttpServer server;

    FakeS3() throws IOException {
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    String endpoint() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    Map<String, byte[]> objects() {
        return objects;
    }

    synchronized List<String> requests() {
        return List.copyOf(requests);
    }

    int refusals() {
        return refusals.get();
    }

    @Override
    public void close() {
        server.stop(0);
    }

    // ------------------------------------------------------------ 处理

    private void handle(HttpExchange exchange) throws IOException {
        byte[] body = exchange.getRequestBody().readAllBytes();
        synchronized (this) {
            requests.add(exchange.getRequestMethod() + " " + exchange.getRequestURI().getRawPath()
                    + (exchange.getRequestURI().getRawQuery() == null ? ""
                            : "?" + exchange.getRequestURI().getRawQuery()));
        }
        String reason = verify(exchange, body);
        if (reason != null) {
            refusals.incrementAndGet();
            respond(exchange, 403, ("<Error><Code>SignatureDoesNotMatch</Code><Message>" + reason
                    + "</Message></Error>").getBytes(StandardCharsets.UTF_8));
            return;
        }
        String path = URLDecoder.decode(exchange.getRequestURI().getRawPath(), StandardCharsets.UTF_8);
        String query = exchange.getRequestURI().getRawQuery();
        if (query != null && query.contains("list-type=2")) {
            list(exchange, query);
            return;
        }
        String key = path.startsWith("/" + BUCKET + "/") ? path.substring(BUCKET.length() + 2) : null;
        if (key == null || key.isEmpty()) {
            respond(exchange, 404, new byte[0]);
            return;
        }
        switch (exchange.getRequestMethod()) {
            case "PUT" -> {
                objects.put(key, body);
                respond(exchange, 200, new byte[0]);
            }
            case "GET" -> get(exchange, key);
            case "HEAD" -> head(exchange, key);
            case "DELETE" -> {
                objects.remove(key);
                respond(exchange, 204, new byte[0]);
            }
            default -> respond(exchange, 405, new byte[0]);
        }
    }

    private void get(HttpExchange exchange, String key) throws IOException {
        byte[] content = objects.get(key);
        if (content == null) {
            respond(exchange, 404, "<Error><Code>NoSuchKey</Code></Error>".getBytes(StandardCharsets.UTF_8));
            return;
        }
        respond(exchange, 200, content);
    }

    private void head(HttpExchange exchange, String key) throws IOException {
        byte[] content = objects.get(key);
        if (content == null) {
            exchange.sendResponseHeaders(404, -1);
            exchange.close();
            return;
        }
        exchange.getResponseHeaders().set("Content-Length", Integer.toString(content.length));
        // HEAD 不带体：sendResponseHeaders(-1) 会让 JDK 自己算长度，所以显式用 0 并手动写头。
        exchange.sendResponseHeaders(200, -1);
        exchange.close();
    }

    /** ListObjectsV2，一次最多两个键，好让被测实现的续读真的被走到。 */
    private void list(HttpExchange exchange, String query) throws IOException {
        Map<String, String> params = new LinkedHashMap<>();
        for (String pair : query.split("&")) {
            int eq = pair.indexOf('=');
            params.put(URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8),
                    URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
        }
        String prefix = params.getOrDefault("prefix", "");
        String after = params.getOrDefault("continuation-token", "");
        List<String> keys = new ArrayList<>(new TreeMap<>(objects).keySet().stream()
                .filter(key -> key.startsWith(prefix)).filter(key -> key.compareTo(after) > 0).toList());
        boolean truncated = keys.size() > 2;
        List<String> page = truncated ? keys.subList(0, 2) : keys;
        StringBuilder xml = new StringBuilder("<?xml version=\"1.0\"?><ListBucketResult>");
        page.forEach(key -> xml.append("<Contents><Key>").append(escape(key)).append("</Key></Contents>"));
        xml.append("<IsTruncated>").append(truncated).append("</IsTruncated>");
        if (truncated) {
            xml.append("<NextContinuationToken>").append(escape(page.get(page.size() - 1)))
                    .append("</NextContinuationToken>");
        }
        xml.append("</ListBucketResult>");
        respond(exchange, 200, xml.toString().getBytes(StandardCharsets.UTF_8));
    }

    private static String escape(String value) {
        return value.replace("&", "&amp;").replace("<", "&lt;");
    }

    private static void respond(HttpExchange exchange, int status, byte[] body) throws IOException {
        exchange.sendResponseHeaders(status, body.length == 0 ? -1 : body.length);
        if (body.length > 0) {
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(body);
            }
        }
        exchange.close();
    }

    // ------------------------------------------------------------ 独立实现的 SigV4 校验

    /** @return null ＝ 通过；否则是拒绝的原因（只给测试看） */
    private static String verify(HttpExchange exchange, byte[] body) {
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        if (authorization == null || !authorization.startsWith(ALGORITHM + " ")) {
            return "missing authorization";
        }
        Map<String, String> parts = new LinkedHashMap<>();
        for (String piece : authorization.substring(ALGORITHM.length() + 1).split(",")) {
            int eq = piece.indexOf('=');
            parts.put(piece.substring(0, eq).trim(), piece.substring(eq + 1).trim());
        }
        String credential = parts.get("Credential");
        String signedHeaders = parts.get("SignedHeaders");
        String signature = parts.get("Signature");
        if (credential == null || signedHeaders == null || signature == null) {
            return "incomplete authorization";
        }
        String amzDate = exchange.getRequestHeaders().getFirst("x-amz-date");
        String payloadHash = exchange.getRequestHeaders().getFirst("x-amz-content-sha256");
        if (amzDate == null || payloadHash == null) {
            return "missing signed headers";
        }
        if (!payloadHash.equals(hex(sha256(body)))) {
            // 签的必须是真正发出去的那些字节。
            return "payload hash does not match the body";
        }
        String date = amzDate.substring(0, 8);
        String scope = date + "/" + REGION + "/" + SERVICE + "/aws4_request";
        if (!credential.equals(ACCESS_KEY + "/" + scope)) {
            return "unexpected credential scope: " + credential;
        }
        String canonicalRequest = canonicalRequest(exchange, signedHeaders, payloadHash);
        String stringToSign = ALGORITHM + "\n" + amzDate + "\n" + scope + "\n" + hex(sha256(
                canonicalRequest.getBytes(StandardCharsets.UTF_8)));
        String expected = hex(hmac(signingKey(date), stringToSign.getBytes(StandardCharsets.UTF_8)));
        return expected.equals(signature) ? null : "signature mismatch";
    }

    private static String canonicalRequest(HttpExchange exchange, String signedHeaders, String payloadHash) {
        StringBuilder headers = new StringBuilder();
        for (String name : signedHeaders.split(";")) {
            String value = "host".equals(name)
                    ? exchange.getRequestHeaders().getFirst("Host")
                    : exchange.getRequestHeaders().getFirst(name);
            headers.append(name).append(':').append(value == null ? "" : value.trim()).append('\n');
        }
        return exchange.getRequestMethod() + "\n"
                + exchange.getRequestURI().getRawPath() + "\n"
                + canonicalQuery(exchange.getRequestURI().getRawQuery()) + "\n"
                + headers + "\n"
                + signedHeaders + "\n"
                + payloadHash;
    }

    private static String canonicalQuery(String raw) {
        if (raw == null || raw.isEmpty()) {
            return "";
        }
        TreeMap<String, String> sorted = new TreeMap<>();
        for (String pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            sorted.put(pair.substring(0, eq), pair.substring(eq + 1));
        }
        StringBuilder canonical = new StringBuilder();
        sorted.forEach((key, value) -> canonical.append(canonical.length() == 0 ? "" : "&")
                .append(key).append('=').append(value));
        return canonical.toString();
    }

    private static byte[] signingKey(String date) {
        byte[] key = hmac(("AWS4" + SECRET_KEY).getBytes(StandardCharsets.UTF_8),
                date.getBytes(StandardCharsets.UTF_8));
        key = hmac(key, REGION.getBytes(StandardCharsets.UTF_8));
        key = hmac(key, SERVICE.getBytes(StandardCharsets.UTF_8));
        return hmac(key, "aws4_request".getBytes(StandardCharsets.UTF_8));
    }

    private static byte[] hmac(byte[] key, byte[] message) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(message);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static byte[] sha256(byte[] bytes) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(bytes);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static String hex(byte[] bytes) {
        return HexFormat.of().formatHex(bytes);
    }
}
