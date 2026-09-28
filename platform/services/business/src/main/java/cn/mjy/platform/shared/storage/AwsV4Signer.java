package cn.mjy.platform.shared.storage;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.Map;
import java.util.TreeMap;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * AWS 签名版本 4（S3 兼容对象存储用）。纯 JDK，不引依赖——与发布网关"只用标准库"的取向一致，
 * 也避免为了几百行签名把整个 AWS SDK（及其传递依赖）拖进平台。
 *
 * <pre>
 * 规范化请求 = 方法 \n 路径 \n 查询串 \n 头 \n 已签头名 \n 载荷哈希
 * 待签串     = "AWS4-HMAC-SHA256" \n 时间 \n 范围 \n hex(sha256(规范化请求))
 * 签名密钥   = HMAC(HMAC(HMAC(HMAC("AWS4"+密钥, 日期), 区域), "s3"), "aws4_request")
 * </pre>
 *
 * <p><b>载荷一律实签</b>（不用 {@code UNSIGNED-PAYLOAD}）：这要求上传在发出之前就知道
 * 全部字节，正是 {@link S3BlobStore} 先落临时文件再 PUT 的原因之一。实签让签名覆盖内容，
 * 私有化部署里常见的 HTTP 端点也因此不至于让中间人改掉对象内容。
 */
final class AwsV4Signer {

    static final String ALGORITHM = "AWS4-HMAC-SHA256";
    static final String SERVICE = "s3";
    static final String DATE_HEADER = "x-amz-date";
    static final String CONTENT_SHA256_HEADER = "x-amz-content-sha256";

    private static final DateTimeFormatter AMZ_DATE =
            DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'").withZone(ZoneOffset.UTC);
    private static final String TERMINATOR = "aws4_request";
    private static final String HMAC = "HmacSHA256";

    private final String accessKeyId;
    private final String secretAccessKey;
    private final String region;

    AwsV4Signer(String accessKeyId, String secretAccessKey, String region) {
        this.accessKeyId = accessKeyId;
        this.secretAccessKey = secretAccessKey;
        this.region = region;
    }

    /** 一次签名要带的三个头：{@code x-amz-date}、{@code x-amz-content-sha256}、{@code Authorization}。 */
    record SignedHeaders(String amzDate, String payloadHash, String authorization) {
    }

    /**
     * @param canonicalUri   已百分号编码的路径（{@code /} 不编码）
     * @param canonicalQuery 已按参数名升序、各自编码的查询串；没有就传空串
     * @param payloadHash    请求体的 hex(sha256)；空体用空串的哈希
     */
    SignedHeaders sign(String method, String host, String canonicalUri, String canonicalQuery, String payloadHash,
            Instant now) {
        String amzDate = AMZ_DATE.format(now);
        String date = amzDate.substring(0, 8);
        Map<String, String> headers = new TreeMap<>();
        headers.put("host", host);
        headers.put(CONTENT_SHA256_HEADER, payloadHash);
        headers.put(DATE_HEADER, amzDate);
        String signedHeaderNames = String.join(";", headers.keySet());
        StringBuilder canonicalHeaders = new StringBuilder();
        headers.forEach((name, value) -> canonicalHeaders.append(name).append(':').append(value).append('\n'));

        String canonicalRequest = method + "\n" + canonicalUri + "\n" + canonicalQuery + "\n"
                + canonicalHeaders + "\n" + signedHeaderNames + "\n" + payloadHash;
        String scope = date + "/" + region + "/" + SERVICE + "/" + TERMINATOR;
        String stringToSign = ALGORITHM + "\n" + amzDate + "\n" + scope + "\n"
                + hex(sha256(canonicalRequest.getBytes(StandardCharsets.UTF_8)));
        String signature = hex(hmac(signingKey(date), stringToSign.getBytes(StandardCharsets.UTF_8)));
        String authorization = ALGORITHM + " Credential=" + accessKeyId + "/" + scope
                + ", SignedHeaders=" + signedHeaderNames + ", Signature=" + signature;

        return new SignedHeaders(amzDate, payloadHash, authorization);
    }

    private byte[] signingKey(String date) {
        byte[] key = hmac(("AWS4" + secretAccessKey).getBytes(StandardCharsets.UTF_8),
                date.getBytes(StandardCharsets.UTF_8));
        key = hmac(key, region.getBytes(StandardCharsets.UTF_8));
        key = hmac(key, SERVICE.getBytes(StandardCharsets.UTF_8));
        return hmac(key, TERMINATOR.getBytes(StandardCharsets.UTF_8));
    }

    static String hex(byte[] bytes) {
        return HexFormat.of().formatHex(bytes);
    }

    static byte[] sha256(byte[] bytes) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(bytes);
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 is required by every JDK", e);
        }
    }

    static MessageDigest newSha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 is required by every JDK", e);
        }
    }

    private static byte[] hmac(byte[] key, byte[] message) {
        try {
            Mac mac = Mac.getInstance(HMAC);
            mac.init(new SecretKeySpec(key, HMAC));
            return mac.doFinal(message);
        } catch (Exception e) {
            throw new IllegalStateException("HmacSHA256 is required by every JDK", e);
        }
    }
}
