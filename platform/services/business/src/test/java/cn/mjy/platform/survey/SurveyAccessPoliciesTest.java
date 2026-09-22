package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.Base64;
import java.util.UUID;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * WP-04 访问策略在草稿保存时的处理（ADR 0016 决定 3、5）：明文密码换成 PBKDF2 哈希、明文不落库；
 * 时间窗没写时区时填入默认 Asia/Shanghai。其余语义校验由发布网关负责（422）。
 */
class SurveyAccessPoliciesTest {

    private static final String PLAINTEXT = "Open-Sesame-7";
    private static final UUID SURVEY = UUID.fromString("0b7d9a3e-6c1f-4e57-9b8a-0f3f5a2e1c11");

    private final JsonMapper json = JsonMapper.builder().build();
    private final SurveyDefinitions definitions = new SurveyDefinitions(json);

    @Test
    void plaintextPasswordIsReplacedByAPbkdf2HashThatVerifies() throws Exception {
        ObjectNode normalized = definitions.normalize(withPolicy("{\"policyVersion\":1,\"access\":{\"password\":\"" + PLAINTEXT + "\"}}"), SURVEY);

        JsonNode access = normalized.get("policy").get("access");
        assertThat(access.has("password")).isFalse();
        String[] parts = access.get("passwordHash").asString().split("\\$");
        assertThat(parts).hasSize(4);
        assertThat(parts[0]).isEqualTo("pbkdf2-sha256");
        assertThat(Integer.parseInt(parts[1])).isGreaterThanOrEqualTo(310_000);
        byte[] salt = Base64.getDecoder().decode(parts[2]);
        assertThat(salt).hasSize(16);
        assertThat(Base64.getDecoder().decode(parts[3])).isEqualTo(pbkdf2(PLAINTEXT, salt, Integer.parseInt(parts[1])));
    }

    @Test
    void plaintextNeverSurvivesSerialization() {
        ObjectNode normalized = definitions.normalize(withPolicy("{\"policyVersion\":1,\"access\":{\"password\":\"" + PLAINTEXT + "\"}}"), SURVEY);

        assertThat(definitions.serialize(normalized)).doesNotContain(PLAINTEXT);
    }

    @Test
    void eachSaveSaltsAfreshAndAStoredHashIsKeptAsIs() {
        ObjectNode first = definitions.normalize(withPolicy("{\"policyVersion\":1,\"access\":{\"password\":\"x-" + PLAINTEXT + "\"}}"), SURVEY);
        ObjectNode second = definitions.normalize(withPolicy("{\"policyVersion\":1,\"access\":{\"password\":\"x-" + PLAINTEXT + "\"}}"), SURVEY);
        ObjectNode again = definitions.normalize(first, SURVEY);

        String hash = first.get("policy").get("access").get("passwordHash").asString();
        assertThat(second.get("policy").get("access").get("passwordHash").asString()).isNotEqualTo(hash);
        assertThat(again.get("policy").get("access").get("passwordHash").asString()).isEqualTo(hash);
    }

    @Test
    void blankOrNonTextPasswordIsRejectedWithoutEchoingIt() {
        assertThatThrownBy(() -> definitions.normalize(withPolicy("{\"policyVersion\":1,\"access\":{\"password\":\"  \"}}"), SURVEY))
                .isInstanceOfSatisfying(InvalidDefinitionException.class,
                        e -> assertThat(e.problems()).containsExactly("policy.access.password must be a non-blank string of at most 128 characters"));
        assertThatThrownBy(() -> definitions.normalize(withPolicy("{\"policyVersion\":1,\"access\":{\"password\":12345}}"), SURVEY))
                .isInstanceOf(InvalidDefinitionException.class);
    }

    @Test
    void windowWithoutTimezoneGetsTheChinaDefault() {
        ObjectNode normalized = definitions.normalize(withPolicy("{\"policyVersion\":1,\"window\":{\"opensAt\":\"2026-10-01T09:00\"}}"), SURVEY);

        assertThat(normalized.get("policy").get("window").get("timezone").asString()).isEqualTo("Asia/Shanghai");
    }

    @Test
    void explicitTimezoneIsKeptAndAnUnknownOneIsRejected() {
        ObjectNode normalized = definitions.normalize(
                withPolicy("{\"policyVersion\":1,\"window\":{\"opensAt\":\"2026-10-01T09:00\",\"timezone\":\"America/New_York\"}}"), SURVEY);

        assertThat(normalized.get("policy").get("window").get("timezone").asString()).isEqualTo("America/New_York");
        assertThatThrownBy(() -> definitions.normalize(
                withPolicy("{\"policyVersion\":1,\"window\":{\"opensAt\":\"2026-10-01T09:00\",\"timezone\":\"Asia/Beijing\"}}"), SURVEY))
                .isInstanceOfSatisfying(InvalidDefinitionException.class,
                        e -> assertThat(e.problems()).containsExactly("policy.window.timezone must be an IANA time zone such as Asia/Shanghai"));
    }

    @Test
    void policyMustBeAnObject() {
        assertThatThrownBy(() -> definitions.normalize(withPolicy("[1,2]"), SURVEY))
                .isInstanceOfSatisfying(InvalidDefinitionException.class,
                        e -> assertThat(e.problems()).containsExactly("policy must be an object"));
    }

    @Test
    void otherRulesPassThroughForTheGatewayToValidate() {
        String limits = "{\"responses\":[{\"by\":\"device\",\"max\":1}],\"maxDurationSeconds\":1800}";
        ObjectNode normalized = definitions.normalize(withPolicy("{\"policyVersion\":1,\"limits\":" + limits + "}"), SURVEY);

        assertThat(normalized.get("policy").get("limits")).isEqualTo(json.readTree(limits));
    }

    @Test
    void definitionWithoutPolicyIsUntouched() {
        ObjectNode normalized = definitions.normalize(base(), SURVEY);

        assertThat(normalized.has("policy")).isFalse();
    }

    private ObjectNode withPolicy(String policy) {
        ObjectNode definition = base();
        definition.set("policy", json.readTree(policy));
        return definition;
    }

    private ObjectNode base() {
        return (ObjectNode) json.readTree("""
                {"definitionVersion":1,"title":"策略","language":"zh-Hans",
                 "groups":[{"uuid":"6a1d8f0e-2b3c-4d5e-8f90-a1b2c3d4e5f6","title":"G",
                   "questions":[{"uuid":"7b2e9f1d-3c4d-4e5f-9a01-b2c3d4e5f6a7","code":"Q1","type":"S","text":"说点什么"}]}]}
                """);
    }

    private static byte[] pbkdf2(String password, byte[] salt, int iterations) throws Exception {
        PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), salt, iterations, 256);
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
    }
}
