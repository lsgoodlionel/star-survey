package cn.mjy.platform.shared.security;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.security.SignedParameters.Verdict;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;

/** 签名渠道参数（R05-08）：参数不可篡改、不可增删、过期即失效、换上下文或换租户密钥都验不过。 */
class SignedParametersTest {

    private static final SecretKeySpec KEY =
            new SecretKeySpec("a-delivery-key-of-at-least-32-bytes!!".getBytes(StandardCharsets.UTF_8), "HmacSHA256");
    private static final SecretKeySpec OTHER_KEY =
            new SecretKeySpec("another-tenant-key-at-least-32-bytes!".getBytes(StandardCharsets.UTF_8), "HmacSHA256");
    private static final String CONTEXT = "link/v1/3f2a0c1e-0000-0000-0000-000000000001";
    private static final Instant NOW = Instant.parse("2026-09-23T10:00:00Z");
    private static final Instant EXPIRES = NOW.plusSeconds(3600);

    private static Map<String, String> params() {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("src", "wechat");
        params.put("dept", "研发部");
        params.put("uid", "e-1001");
        return params;
    }

    private static Map<String, String> signed() {
        return SignedParameters.sign(KEY, CONTEXT, params(), EXPIRES);
    }

    @Test
    void acceptsParametersItSignedItself() {
        assertThat(SignedParameters.verify(KEY, CONTEXT, signed(), NOW)).isEqualTo(Verdict.VALID);
    }

    @Test
    void rejectsATamperedValue() {
        Map<String, String> presented = new LinkedHashMap<>(signed());
        presented.put("src", "sms");
        assertThat(SignedParameters.verify(KEY, CONTEXT, presented, NOW)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsAnAddedParameter() {
        Map<String, String> presented = new LinkedHashMap<>(signed());
        presented.put("role", "admin");
        assertThat(SignedParameters.verify(KEY, CONTEXT, presented, NOW)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsARemovedParameter() {
        Map<String, String> presented = new LinkedHashMap<>(signed());
        presented.remove("dept");
        assertThat(SignedParameters.verify(KEY, CONTEXT, presented, NOW)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsAnExtendedExpiry() {
        Map<String, String> presented = new LinkedHashMap<>(signed());
        presented.put(SignedParameters.EXPIRY_PARAM, Long.toString(EXPIRES.plusSeconds(86_400).getEpochSecond()));
        assertThat(SignedParameters.verify(KEY, CONTEXT, presented, NOW)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsParametersAfterTheirExpiry() {
        assertThat(SignedParameters.verify(KEY, CONTEXT, signed(), EXPIRES.plusSeconds(1)))
                .isEqualTo(Verdict.EXPIRED);
    }

    @Test
    void acceptsParametersInTheLastSecondBeforeExpiry() {
        assertThat(SignedParameters.verify(KEY, CONTEXT, signed(), EXPIRES)).isEqualTo(Verdict.VALID);
    }

    @Test
    void rejectsASignatureMadeForAnotherLink() {
        assertThat(SignedParameters.verify(KEY, CONTEXT + "-other", signed(), NOW)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsASignatureMadeWithAnotherTenantsKey() {
        assertThat(SignedParameters.verify(OTHER_KEY, CONTEXT, signed(), NOW)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsParametersWithoutASignature() {
        Map<String, String> presented = new LinkedHashMap<>(signed());
        presented.remove(SignedParameters.SIGNATURE_PARAM);
        assertThat(SignedParameters.verify(KEY, CONTEXT, presented, NOW)).isEqualTo(Verdict.MISSING_SIGNATURE);
    }

    @Test
    void rejectsAMalformedExpiry() {
        Map<String, String> presented = new LinkedHashMap<>(signed());
        presented.put(SignedParameters.EXPIRY_PARAM, "soon");
        assertThat(SignedParameters.verify(KEY, CONTEXT, presented, NOW)).isEqualTo(Verdict.MALFORMED);
    }

    /** 规范化只看参数集合本身，与调用方给出的顺序无关；否则同一组参数会有多个合法签名。 */
    @Test
    void signsTheSameParametersRegardlessOfInsertionOrder() {
        Map<String, String> reversed = new TreeMap<>(java.util.Comparator.reverseOrder());
        reversed.putAll(params());
        assertThat(SignedParameters.sign(KEY, CONTEXT, reversed, EXPIRES))
                .containsEntry(SignedParameters.SIGNATURE_PARAM, signed().get(SignedParameters.SIGNATURE_PARAM));
    }

    /** 分隔符不可被参数内容伪造：{@code a=b&c} 与 {@code a=b}, {@code c=} 必须得到不同签名。 */
    @Test
    void separatorsInsideValuesCannotForgeAnotherParameterSet() {
        Map<String, String> one = new LinkedHashMap<>();
        one.put("a", "b&c=d");
        Map<String, String> two = new LinkedHashMap<>();
        two.put("a", "b");
        two.put("c", "d");
        assertThat(SignedParameters.sign(KEY, CONTEXT, one, EXPIRES).get(SignedParameters.SIGNATURE_PARAM))
                .isNotEqualTo(SignedParameters.sign(KEY, CONTEXT, two, EXPIRES)
                        .get(SignedParameters.SIGNATURE_PARAM));
    }

    @Test
    void refusesToSignReservedParameterNames() {
        Map<String, String> params = params();
        params.put(SignedParameters.SIGNATURE_PARAM, "forged");
        assertThat(SignedParameters.isReserved(SignedParameters.SIGNATURE_PARAM)).isTrue();
        assertThat(SignedParameters.isReserved(SignedParameters.EXPIRY_PARAM)).isTrue();
        assertThat(SignedParameters.isReserved("src")).isFalse();
        org.assertj.core.api.Assertions
                .assertThatThrownBy(() -> SignedParameters.sign(KEY, CONTEXT, params, EXPIRES))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
