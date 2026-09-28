package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * 事件回调的签名与加解密。前几条是开放平台官方示例里的向量（企业微信官方 PHP 库 Sample、钉钉官方
 * DingTalk-Callback-Crypto 仓库的 Python 示例、飞书"Encrypt Key 加密配置示例"文档），证明实现与官方算法一致；
 * 其余是按文档算法构造的往返与篡改用例。
 */
class OrgEventCryptoTest {

    // 企业微信官方示例（https://developer.work.weixin.qq.com/document/path/90968 与官方 weworkapi_php callback/Sample.php）
    private static final String WECOM_TOKEN = "QDG6eK";
    private static final String WECOM_AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C";
    private static final String WECOM_CORP = "wx5823bf96d3bd56c7";
    private static final String WECOM_ECHOSTR =
            "P9nAzCzyDtyTWESHep1vC5X9xho/qYX3Zpb4yKa9SKld1DsH3Iyt3tP3zNdtp+4RPcs8TgAE7OaBO+FZXvnaqQ==";
    private static final String WECOM_ENCRYPT = "RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/0lWfrf0QADHHhGd3QczcdCUpj911L3vg3W/sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wiSpGLYL42aM6A8dTT+6k4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZfJYuR+LtwGc2hgf5gsijff0ekUNXZiqATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQH03v+BCA9nMELNqbSf6tiWSrXJB3LAVGUcallcrw8V2t9EL4EhzJWrQUax5wLVMNS0+rUPA3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCmJU9p8g7m3fVKn28H3KDYA5Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q==";

    @Test
    void theOfficialWeComUrlVerificationVectorVerifiesAndDecrypts() {
        String signature = OrgEventCrypto.sortedSha1(WECOM_TOKEN, "1409659589", "263014780", WECOM_ECHOSTR);

        assertThat(OrgEventCrypto.signatureMatches(signature, "5c45ff5e21c57e6ad56bac8758b79b1d9ac89fd3")).isTrue();
        OrgEventCrypto.Envelope envelope = OrgEventCrypto.openWxBiz(WECOM_AES_KEY, WECOM_ECHOSTR);
        assertThat(envelope.receiver()).isEqualTo(WECOM_CORP);
        assertThat(envelope.message()).matches("\\d+");
    }

    @Test
    void theOfficialWeComMessageVectorVerifiesAndDecrypts() {
        String signature = OrgEventCrypto.sortedSha1(WECOM_TOKEN, "1409659813", "1372623149", WECOM_ENCRYPT);

        assertThat(signature).isEqualTo("477715d11cdb4164915debcba66cb864d751f3e6");
        OrgEventCrypto.Envelope envelope = OrgEventCrypto.openWxBiz(WECOM_AES_KEY, WECOM_ENCRYPT);
        assertThat(envelope.receiver()).isEqualTo(WECOM_CORP);
        assertThat(envelope.message()).startsWith("<xml>").contains("<ToUserName><![CDATA[" + WECOM_CORP);
    }

    /** 钉钉官方仓库 open-dingtalk/DingTalk-Callback-Crypto 的 DingCallbackCrypto3.py 自带的解密用例。 */
    @Test
    void theOfficialDingTalkVectorVerifiesAndDecrypts() {
        String encrypted = "0vJiX6vliEpwG3U45CtXqi+m8PXbQRARJ8p8BbDuD1EMTDf0jKpQ79QS93qEk7XHpP6u+oTTrd15NRPvNvmBKyDCYxxOK+HZeKju4yhELOFchzNukR+t8SB/qk4ROMu3";

        String signature = OrgEventCrypto.sortedSha1("mryue", "1608001896814", "WL4PK6yA", encrypted);

        assertThat(signature).isEqualTo("03044561471240d4a14bb09372dfcfd4fd0e40cb");
        OrgEventCrypto.Envelope envelope = OrgEventCrypto.openWxBiz("Yue0EfdN5900c1ce5cf6A152c63DDe1808a60c5ecd7",
                encrypted);
        assertThat(envelope.message()).isEqualTo("{\"EventType\":\"check_url\"}");
        assertThat(envelope.receiver()).isEqualTo("ding6ccabc44d2c8d38b");
    }

    /** 飞书文档"Encrypt Key 加密配置示例"给出的向量。 */
    @Test
    void theOfficialFeishuVectorDecrypts() {
        assertThat(OrgEventCrypto.openFeishu("test key", "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk="))
                .isEqualTo("hello world");
    }

    /** 企业微信 / 钉钉补位到 32 字节：包括恰好整块时补满 32 个字节的情形。 */
    @ParameterizedTest
    @ValueSource(ints = {0, 1, 11, 12, 13, 31, 32, 100})
    void wxBizEnvelopesRoundTripWithThirtyTwoBytePadding(int length) {
        String message = "m".repeat(length);
        String receiver = "corp1234";

        String sealed = OrgEventCrypto.sealWxBiz(WECOM_AES_KEY, message, receiver);

        assertThat(Base64.getDecoder().decode(sealed).length % OrgEventCrypto.WXBIZ_BLOCK).isZero();
        assertThat(OrgEventCrypto.openWxBiz(WECOM_AES_KEY, sealed))
                .isEqualTo(new OrgEventCrypto.Envelope(message, receiver));
    }

    @Test
    void aTamperedOrForeignCiphertextIsRejected() {
        String otherKey = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
        byte[] raw = Base64.getDecoder().decode(OrgEventCrypto.sealWxBiz(WECOM_AES_KEY, "hello", "corp"));
        raw[raw.length - 1] ^= 0x5a;

        assertThatThrownBy(() -> OrgEventCrypto.openWxBiz(WECOM_AES_KEY, Base64.getEncoder().encodeToString(raw)))
                .isInstanceOf(OrgEventException.class);
        String sealed = OrgEventCrypto.sealWxBiz(WECOM_AES_KEY, "hello", "corp");
        try {
            // 用别的密钥解开：要么补位 / 长度校验失败，要么得到乱码的接收方——绝不会得到原接收方。
            assertThat(OrgEventCrypto.openWxBiz(otherKey, sealed).receiver()).isNotEqualTo("corp");
        } catch (OrgEventException expected) {
            assertThat(expected.status().value()).isEqualTo(401);
        }
        assertThatThrownBy(() -> OrgEventCrypto.openWxBiz(WECOM_AES_KEY, "not base64 !!"))
                .isInstanceOf(OrgEventException.class);
    }

    @Test
    void aMalformedEncodingAesKeyIsAConfigurationError() {
        assertThatThrownBy(() -> OrgEventCrypto.openWxBiz("short", "AAAA"))
                .isInstanceOfSatisfying(OrgEventException.class,
                        e -> assertThat(e.error()).isEqualTo(OrgEventException.NOT_CONFIGURED));
    }

    @Test
    void feishuEnvelopesRoundTripAndTheSignatureCoversTheRawBody() {
        String sealed = OrgEventCrypto.sealFeishu("encrypt-key", "{\"a\":1}");
        byte[] body = ("{\"encrypt\":\"" + sealed + "\"}").getBytes(StandardCharsets.UTF_8);

        assertThat(OrgEventCrypto.openFeishu("encrypt-key", sealed)).isEqualTo("{\"a\":1}");
        String signature = OrgEventCrypto.feishuSignature("1700000000", "nonce", "encrypt-key", body);
        assertThat(signature).matches("[0-9a-f]{64}");
        assertThat(OrgEventCrypto.feishuSignature("1700000000", "nonce", "encrypt-key",
                (new String(body, StandardCharsets.UTF_8) + " ").getBytes(StandardCharsets.UTF_8)))
                .isNotEqualTo(signature);
        // 用别的密钥解开：要么补位 / Base64 校验失败，要么得到乱码——绝不会得到原文。错误密钥只有
        // 255/256 的概率撞上非法 PKCS#5 补位（IV 每次随机），所以断言结果而不是断言一定抛异常。
        try {
            assertThat(OrgEventCrypto.openFeishu("other-key", sealed)).isNotEqualTo("{\"a\":1}");
        } catch (OrgEventException expected) {
            assertThat(expected.status().value()).isEqualTo(401);
        }
    }

    @Test
    void signaturesCompareCaseInsensitivelyAndRejectMissingValues() {
        String signature = OrgEventCrypto.sortedSha1("t", "1", "n", "e");

        assertThat(OrgEventCrypto.signatureMatches(signature, signature.toUpperCase())).isTrue();
        assertThat(OrgEventCrypto.signatureMatches(signature, null)).isFalse();
        assertThat(OrgEventCrypto.signatureMatches(signature, signature.substring(1))).isFalse();
    }
}
