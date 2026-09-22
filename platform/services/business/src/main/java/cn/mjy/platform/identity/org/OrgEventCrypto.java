package cn.mjy.platform.identity.org;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Locale;
import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * 三家开放平台事件回调的签名与加解密（只用 JDK 加密库）。
 *
 * <p>企业微信与钉钉同源（"WXBizMsgCrypt" 方案）：
 * <ul>
 *   <li>签名 = SHA-1(把 token、timestamp、nonce、密文 四个字符串按字典序排序后拼接)，小写十六进制；</li>
 *   <li>AESKey = Base64_Decode(EncodingAESKey + "=")，32 字节；AES-256-CBC，IV = AESKey 前 16 字节；</li>
 *   <li>明文 = random(16B) + msg_len(4B，网络字节序) + msg + receiveid，PKCS#7 补位到 <b>32 字节</b>的倍数；</li>
 *   <li>receiveid：企业微信为 corpid；钉钉企业内部应用事件订阅为 AppKey（注册回调地址旧接口为 corpId）。</li>
 * </ul>
 * 飞书：key = SHA-256(Encrypt Key)，密文 Base64 解码后前 16 字节为 IV，其余 AES-256-CBC + PKCS#7（16 字节块）；
 * 签名 = SHA-256(X-Lark-Request-Timestamp + X-Lark-Request-Nonce + Encrypt Key + 原始请求体)，小写十六进制。
 *
 * <p>所有失败都抛 {@link OrgEventException}（401），消息不含密钥、明文或密文。
 */
final class OrgEventCrypto {

    static final int WXBIZ_BLOCK = 32;
    private static final int RANDOM_BYTES = 16;
    private static final int LENGTH_BYTES = 4;
    private static final int IV_BYTES = 16;
    private static final int AES_KEY_BYTES = 32;
    private static final int ENCODING_AES_KEY_CHARS = 43;
    private static final SecureRandom RANDOM = new SecureRandom();

    /** 解密后的消息与其中携带的接收方标识。 */
    record Envelope(String message, String receiver) {
    }

    private OrgEventCrypto() {
    }

    /** 企业微信 / 钉钉签名：四个参数按字典序排序后拼接，SHA-1 小写十六进制。 */
    static String sortedSha1(String token, String timestamp, String nonce, String encrypted) {
        String[] parts = {token, timestamp, nonce, encrypted};
        Arrays.sort(parts);
        return hex(digest("SHA-1", String.join("", parts).getBytes(StandardCharsets.UTF_8)));
    }

    /** 飞书签名：SHA-256(timestamp + nonce + encryptKey + 原始请求体)，小写十六进制。 */
    static String feishuSignature(String timestamp, String nonce, String encryptKey, byte[] body) {
        byte[] prefix = (timestamp + nonce + encryptKey).getBytes(StandardCharsets.UTF_8);
        byte[] all = Arrays.copyOf(prefix, prefix.length + body.length);
        System.arraycopy(body, 0, all, prefix.length, body.length);
        return hex(digest("SHA-256", all));
    }

    /** 常量时间比较十六进制签名（大小写不敏感）。 */
    static boolean signatureMatches(String expectedHex, String providedHex) {
        if (providedHex == null) {
            return false;
        }
        return MessageDigest.isEqual(expectedHex.getBytes(StandardCharsets.US_ASCII),
                providedHex.toLowerCase(Locale.ROOT).getBytes(StandardCharsets.US_ASCII));
    }

    /** 常量时间比较两个口令（如飞书 Verification Token）。 */
    static boolean tokenMatches(String expected, String provided) {
        return provided != null && MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                provided.getBytes(StandardCharsets.UTF_8));
    }

    /** 企业微信 / 钉钉解密。receiveid 由调用方核对。 */
    static Envelope openWxBiz(String encodingAesKey, String base64Cipher) {
        byte[] key = wxBizKey(encodingAesKey);
        byte[] plain;
        try {
            Cipher cipher = Cipher.getInstance("AES/CBC/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new IvParameterSpec(key, 0, IV_BYTES));
            plain = unpad(cipher.doFinal(base64(base64Cipher)), WXBIZ_BLOCK);
        } catch (GeneralSecurityException | IllegalArgumentException e) {
            throw OrgEventException.unauthenticated("event payload cannot be decrypted");
        }
        if (plain.length < RANDOM_BYTES + LENGTH_BYTES) {
            throw OrgEventException.unauthenticated("event payload is truncated");
        }
        int length = ByteBuffer.wrap(plain, RANDOM_BYTES, LENGTH_BYTES).getInt();
        int start = RANDOM_BYTES + LENGTH_BYTES;
        if (length < 0 || length > plain.length - start) {
            throw OrgEventException.unauthenticated("event payload length is invalid");
        }
        String message = new String(plain, start, length, StandardCharsets.UTF_8);
        String receiver = new String(plain, start + length, plain.length - start - length, StandardCharsets.UTF_8);
        return new Envelope(message, receiver);
    }

    /** 企业微信 / 钉钉加密（钉钉要求把加密后的 "success" 作为应答）。 */
    static String sealWxBiz(String encodingAesKey, String message, String receiver) {
        byte[] key = wxBizKey(encodingAesKey);
        byte[] random = new byte[RANDOM_BYTES];
        RANDOM.nextBytes(random);
        byte[] msg = message.getBytes(StandardCharsets.UTF_8);
        byte[] recv = receiver.getBytes(StandardCharsets.UTF_8);
        ByteBuffer buffer = ByteBuffer.allocate(RANDOM_BYTES + LENGTH_BYTES + msg.length + recv.length);
        buffer.put(random).putInt(msg.length).put(msg).put(recv);
        try {
            Cipher cipher = Cipher.getInstance("AES/CBC/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new IvParameterSpec(key, 0, IV_BYTES));
            return Base64.getEncoder().encodeToString(cipher.doFinal(pad(buffer.array(), WXBIZ_BLOCK)));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("AES unavailable", e);
        }
    }

    /** 飞书解密。 */
    static String openFeishu(String encryptKey, String base64Cipher) {
        try {
            byte[] data = base64(base64Cipher);
            if (data.length <= IV_BYTES) {
                throw OrgEventException.unauthenticated("event payload is truncated");
            }
            byte[] key = digest("SHA-256", encryptKey.getBytes(StandardCharsets.UTF_8));
            Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
            cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"), new IvParameterSpec(data, 0, IV_BYTES));
            return new String(cipher.doFinal(data, IV_BYTES, data.length - IV_BYTES), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException | IllegalArgumentException e) {
            throw OrgEventException.unauthenticated("event payload cannot be decrypted");
        }
    }

    /** 飞书加密（仅供测试构造请求；平台不需要向飞书发加密内容）。 */
    static String sealFeishu(String encryptKey, String message) {
        try {
            byte[] iv = new byte[IV_BYTES];
            RANDOM.nextBytes(iv);
            byte[] key = digest("SHA-256", encryptKey.getBytes(StandardCharsets.UTF_8));
            Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
            cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new IvParameterSpec(iv));
            byte[] body = cipher.doFinal(message.getBytes(StandardCharsets.UTF_8));
            byte[] all = Arrays.copyOf(iv, IV_BYTES + body.length);
            System.arraycopy(body, 0, all, IV_BYTES, body.length);
            return Base64.getEncoder().encodeToString(all);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("AES unavailable", e);
        }
    }

    static String sha256Hex(String value) {
        return hex(digest("SHA-256", value.getBytes(StandardCharsets.UTF_8)));
    }

    /** EncodingAESKey 格式不对是配置错误（而不是请求伪造），按未配置处理。 */
    private static byte[] wxBizKey(String encodingAesKey) {
        if (encodingAesKey == null || encodingAesKey.length() != ENCODING_AES_KEY_CHARS) {
            throw OrgEventException.misconfigured();
        }
        try {
            byte[] key = Base64.getDecoder().decode(encodingAesKey + "=");
            if (key.length != AES_KEY_BYTES) {
                throw OrgEventException.misconfigured();
            }
            return key;
        } catch (IllegalArgumentException e) {
            throw OrgEventException.misconfigured();
        }
    }

    private static byte[] base64(String value) {
        if (value == null || value.isEmpty()) {
            throw OrgEventException.unauthenticated("event payload is missing");
        }
        return Base64.getDecoder().decode(value);
    }

    private static byte[] pad(byte[] data, int block) {
        int padding = block - data.length % block;
        byte[] padded = Arrays.copyOf(data, data.length + padding);
        Arrays.fill(padded, data.length, padded.length, (byte) padding);
        return padded;
    }

    private static byte[] unpad(byte[] data, int block) {
        if (data.length == 0 || data.length % 16 != 0) {
            throw OrgEventException.unauthenticated("event payload padding is invalid");
        }
        int padding = data[data.length - 1] & 0xff;
        if (padding < 1 || padding > block || padding > data.length) {
            throw OrgEventException.unauthenticated("event payload padding is invalid");
        }
        return Arrays.copyOf(data, data.length - padding);
    }

    private static byte[] digest(String algorithm, byte[] data) {
        try {
            return MessageDigest.getInstance(algorithm).digest(data);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(algorithm + " unavailable", e);
        }
    }

    private static String hex(byte[] bytes) {
        return HexFormat.of().formatHex(bytes);
    }
}
