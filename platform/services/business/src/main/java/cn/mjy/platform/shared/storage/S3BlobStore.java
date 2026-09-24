package cn.mjy.platform.shared.storage;

import java.io.BufferedOutputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * S3 兼容对象存储实现（MinIO、Ceph RGW、各家兼容 S3 接口的对象存储）。
 * <b>导出文件与资产字节共用它</b>——两处的"多副本需要共享卷"是同一条遗留
 * （ADR 0015 决定 2、ADR 0019 决定 2），所以只做一次实现，两边同时切过去。
 *
 * <p>两个模块在同一个桶里各占一个键前缀（{@code exports/} / {@code assets/}），
 * 由构造参数 {@code keyPrefix} 给出。平台生成的键本身不含调用方文本
 * （{@link BlobStore#requireKey}），前缀只是再加一层命名空间。
 *
 * <h2>上传为什么先落临时文件</h2>
 *
 * {@link BlobStore.Upload} 的语义是"往流里写，提交之后对象才出现"。S3 的单次 PUT 要求
 * <b>发出之前就知道长度与载荷哈希</b>，而调用方是边算边写的（导出把行流式编码进 ZIP）。
 * 因此写入先落到本机临时文件，{@link Upload#commit()} 时再整份 PUT 上去。三个后果：
 *
 * <ul>
 *   <li>提交之前对面一个字节都没收到——与本地实现"写临时文件再原子改名"完全同一套可见性；</li>
 *   <li>载荷<b>实签</b>（不用 {@code UNSIGNED-PAYLOAD}），签名覆盖内容；</li>
 *   <li>代价：本机需要能放下**单个**最大对象的临时空间。它是每副本各自的临时目录，
 *       不是需要共享的卷，所以"多副本需共享卷"这条遗留仍然是解掉的。</li>
 * </ul>
 *
 * 分片上传（multipart）能去掉这份临时文件，但要维护分片状态与失败清理，是另一片工作；
 * 单次 PUT 的 5 GiB 上限记在 ADR 的残余风险里。
 *
 * <p><b>故障不等于缺失</b>：只有 404 才是"没有这个对象"，其余非 2xx 一律抛出。
 * 把 403（密钥配错）当成"对象不存在"会让一次配置错误变成一场静默的数据丢失。
 */
public class S3BlobStore implements BlobStore {

    private static final String TEMP_PREFIX = "mjy-blob-";
    private static final String TEMP_SUFFIX = ".partial";
    private static final int BUFFER_BYTES = 64 * 1024;
    private static final int HTTP_NOT_FOUND = 404;
    private static final int LIST_PAGE_SIZE = 1000;
    private static final Pattern LIST_KEY = Pattern.compile("<Key>(.*?)</Key>", Pattern.DOTALL);
    private static final Pattern LIST_TRUNCATED = Pattern.compile("<IsTruncated>\\s*true\\s*</IsTruncated>");
    private static final Pattern LIST_TOKEN =
            Pattern.compile("<NextContinuationToken>(.*?)</NextContinuationToken>", Pattern.DOTALL);

    private final BlobStoreProperties properties;
    private final String keyPrefix;
    private final AwsV4Signer signer;
    private final HttpClient http;
    private final URI endpoint;

    public S3BlobStore(BlobStoreProperties properties, String keyPrefix) {
        this.properties = properties;
        this.keyPrefix = normalizePrefix(keyPrefix);
        this.signer = new AwsV4Signer(properties.accessKeyId(), properties.secretAccessKey(), properties.region());
        this.http = HttpClient.newBuilder().connectTimeout(properties.timeout()).build();
        this.endpoint = URI.create(properties.endpoint());
    }

    /** 这个实例在桶里占的那一段（带结尾斜杠，没有前缀时为空串）。接线测试与诊断用。 */
    public String keyPrefix() {
        return keyPrefix;
    }

    private static String normalizePrefix(String prefix) {
        if (prefix == null || prefix.isBlank()) {
            return "";
        }
        String trimmed = prefix.replaceAll("^/+", "").replaceAll("/+$", "");
        return trimmed.isEmpty() ? "" : trimmed + "/";
    }

    @Override
    public Upload create(String key) throws IOException {
        String object = objectKey(key);
        Path temp = Files.createTempFile(TEMP_PREFIX, TEMP_SUFFIX);
        return new BufferedUpload(temp, object);
    }

    @Override
    public InputStream open(String key) throws IOException {
        HttpResponse<InputStream> response = send("GET", objectKey(key), null, empty(),
                HttpResponse.BodyHandlers.ofInputStream());
        if (response.statusCode() == HTTP_NOT_FOUND) {
            response.body().close();
            throw new FileNotFoundException("no such object: " + key);
        }
        if (response.statusCode() / 100 != 2) {
            response.body().close();
            throw failed("GET", key, response.statusCode());
        }
        return response.body();
    }

    @Override
    public boolean exists(String key) {
        try {
            HttpResponse<Void> response = send("HEAD", objectKey(key), null, empty(),
                    HttpResponse.BodyHandlers.discarding());
            if (response.statusCode() == HTTP_NOT_FOUND) {
                return false;
            }
            if (response.statusCode() / 100 != 2) {
                throw failed("HEAD", key, response.statusCode());
            }
            return true;
        } catch (IOException e) {
            // 接口不允许抛受检异常，但"问不到"绝不能答成"不存在"。
            throw new UncheckedIOException(e);
        }
    }

    @Override
    public long size(String key) throws IOException {
        HttpResponse<Void> response = send("HEAD", objectKey(key), null, empty(),
                HttpResponse.BodyHandlers.discarding());
        if (response.statusCode() == HTTP_NOT_FOUND) {
            throw new FileNotFoundException("no such object: " + key);
        }
        if (response.statusCode() / 100 != 2) {
            throw failed("HEAD", key, response.statusCode());
        }
        return response.headers().firstValueAsLong("Content-Length")
                .orElseThrow(() -> new IOException("object store gave no content length for " + key));
    }

    @Override
    public void delete(String key) throws IOException {
        HttpResponse<Void> response = send("DELETE", objectKey(key), null, empty(),
                HttpResponse.BodyHandlers.discarding());
        if (response.statusCode() / 100 != 2 && response.statusCode() != HTTP_NOT_FOUND) {
            throw failed("DELETE", key, response.statusCode());
        }
    }

    /**
     * 按前缀删：列举再逐个删。前缀按<b>整段</b>匹配（补上结尾的 {@code /}），
     * 否则 {@code job-1} 会顺手把 {@code job-10} 也删掉。
     */
    @Override
    public void deletePrefix(String prefix) throws IOException {
        String base = objectKey(prefix);
        // 前缀本身也可能是一个对象（本地实现里 deleteIfExists 会删掉它）。
        delete(prefix);
        String token = null;
        do {
            String listing = list(base + "/", token);
            for (String object : keysOf(listing)) {
                deleteObject(object);
            }
            token = nextToken(listing);
        } while (token != null);
    }

    // ------------------------------------------------------------ 内部

    private String objectKey(String key) {
        return keyPrefix + BlobStore.requireKey(key);
    }

    private void deleteObject(String object) throws IOException {
        HttpResponse<Void> response = send("DELETE", object, null, empty(),
                HttpResponse.BodyHandlers.discarding());
        if (response.statusCode() / 100 != 2 && response.statusCode() != HTTP_NOT_FOUND) {
            throw failed("DELETE", object, response.statusCode());
        }
    }

    private String list(String prefix, String continuationToken) throws IOException {
        StringBuilder query = new StringBuilder("continuation-token=");
        query.append(continuationToken == null ? "" : encode(continuationToken));
        query.append("&list-type=2&max-keys=").append(LIST_PAGE_SIZE);
        query.append("&prefix=").append(encode(prefix));
        if (continuationToken == null) {
            // 规范化查询串按参数名升序；没有续读令牌时那一项整个不出现。
            query = new StringBuilder("list-type=2&max-keys=" + LIST_PAGE_SIZE + "&prefix=" + encode(prefix));
        }
        HttpResponse<String> response = send("GET", null, query.toString(), empty(),
                HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() / 100 != 2) {
            throw failed("LIST", prefix, response.statusCode());
        }
        return response.body();
    }

    private static List<String> keysOf(String listing) {
        List<String> keys = new ArrayList<>();
        Matcher matcher = LIST_KEY.matcher(listing);
        while (matcher.find()) {
            keys.add(unescape(matcher.group(1)));
        }
        return keys;
    }

    private static String nextToken(String listing) {
        if (!LIST_TRUNCATED.matcher(listing).find()) {
            return null;
        }
        Matcher matcher = LIST_TOKEN.matcher(listing);
        return matcher.find() ? unescape(matcher.group(1)) : null;
    }

    private static String unescape(String value) {
        return value.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
                .replace("&apos;", "'").replace("&amp;", "&");
    }

    private static String empty() {
        return AwsV4Signer.hex(AwsV4Signer.sha256(new byte[0]));
    }

    /**
     * 发一次已签名的请求。{@code object} 为 null 表示打在桶上（列举）。
     * 请求体只有 PUT 用得上，由 {@link #put} 单独走（它要带文件作为 body）。
     */
    private <T> HttpResponse<T> send(String method, String object, String query, String payloadHash,
            HttpResponse.BodyHandler<T> handler) throws IOException {
        HttpRequest request = request(method, object, query, payloadHash, HttpRequest.BodyPublishers.noBody());
        return exchange(request, handler);
    }

    private HttpRequest request(String method, String object, String query, String payloadHash,
            HttpRequest.BodyPublisher body) {
        String path = canonicalUri(object);
        String host = endpoint.getHost() + (endpoint.getPort() < 0 ? "" : ":" + endpoint.getPort());
        AwsV4Signer.SignedHeaders signed = signer.sign(method, host, path, query == null ? "" : query,
                payloadHash, Instant.now());
        URI uri = URI.create(endpoint + path + (query == null || query.isEmpty() ? "" : "?" + query));
        return HttpRequest.newBuilder(uri)
                .timeout(properties.timeout())
                .header(AwsV4Signer.DATE_HEADER, signed.amzDate())
                .header(AwsV4Signer.CONTENT_SHA256_HEADER, signed.payloadHash())
                .header("Authorization", signed.authorization())
                .method(method, body)
                .build();
    }

    /** 路径风格：{@code /<桶>/<键>}；虚拟主机风格由端点本身带桶名，路径只有键。 */
    private String canonicalUri(String object) {
        String path = properties.pathStyle() ? "/" + properties.bucket() : "";
        if (object == null) {
            return path.isEmpty() ? "/" : path;
        }
        StringBuilder encoded = new StringBuilder(path);
        for (String segment : object.split("/", -1)) {
            encoded.append('/').append(encode(segment));
        }
        return encoded.toString();
    }

    /** RFC 3986 未保留字符之外一律百分号编码（{@code /} 由调用方分段处理）。 */
    private static String encode(String value) {
        StringBuilder encoded = new StringBuilder(value.length());
        for (byte b : value.getBytes(java.nio.charset.StandardCharsets.UTF_8)) {
            char c = (char) (b & 0xFF);
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')
                    || c == '-' || c == '_' || c == '.' || c == '~') {
                encoded.append(c);
            } else {
                encoded.append('%').append(String.format("%02X", b & 0xFF));
            }
        }
        return encoded.toString();
    }

    private <T> HttpResponse<T> exchange(HttpRequest request, HttpResponse.BodyHandler<T> handler)
            throws IOException {
        try {
            return http.send(request, handler);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted while talking to the object store", e);
        }
    }

    private void put(Path file, String object) throws IOException {
        String payloadHash = AwsV4Signer.hex(digestOf(file));
        HttpRequest request = request("PUT", object, null, payloadHash,
                HttpRequest.BodyPublishers.ofFile(file));
        HttpResponse<Void> response = exchange(request, HttpResponse.BodyHandlers.discarding());
        if (response.statusCode() / 100 != 2) {
            throw failed("PUT", object, response.statusCode());
        }
    }

    private static byte[] digestOf(Path file) throws IOException {
        MessageDigest digest = AwsV4Signer.newSha256();
        byte[] buffer = new byte[BUFFER_BYTES];
        try (InputStream in = Files.newInputStream(file)) {
            int read;
            while ((read = in.read(buffer)) >= 0) {
                digest.update(buffer, 0, read);
            }
        }
        return digest.digest();
    }

    private static IOException failed(String what, String key, int status) {
        // 不带响应体：对象存储的错误体可能含桶名、请求 id 等部署信息。
        return new IOException("object store " + what + " on " + key + " returned http " + status);
    }

    /** 先落本机临时文件，提交时整份 PUT；没提交就关闭 ＝ 一个字节都没发出去。 */
    private final class BufferedUpload implements Upload {

        private final Path temp;
        private final String object;
        private final OutputStream out;
        private boolean finished;

        private BufferedUpload(Path temp, String object) throws IOException {
            this.temp = temp;
            this.object = object;
            this.out = new BufferedOutputStream(Files.newOutputStream(temp), BUFFER_BYTES);
        }

        @Override
        public OutputStream stream() {
            return out;
        }

        @Override
        public void commit() throws IOException {
            if (finished) {
                throw new IllegalStateException("upload already finished");
            }
            finished = true;
            out.close();
            try {
                put(temp, object);
            } finally {
                Files.deleteIfExists(temp);
            }
        }

        @Override
        public void close() throws IOException {
            if (finished) {
                return;
            }
            finished = true;
            try {
                out.close();
            } finally {
                Files.deleteIfExists(temp);
            }
        }
    }
}
