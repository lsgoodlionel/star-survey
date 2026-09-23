package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 平台重算的 {@code policyDigest} 必须和网关算出来的逐字一致。
 *
 * <p>向量表 {@code src/test/resources/policy/digest-vectors.json} 由网关的
 * {@code pubgw/policy/compile.py} 生成，同一份文件也被网关的
 * {@code tests/test_policy_digest_vectors.py} 读取：改了任何一端而不改另一端，就会有一边红。
 */
class AccessPolicyDigestTest {

    private static final String VECTORS = "/policy/digest-vectors.json";

    private final JsonMapper json = JsonMapper.builder().build();

    private JsonNode vectors() {
        try (InputStream in = AccessPolicyDigestTest.class.getResourceAsStream(VECTORS)) {
            if (in == null) {
                throw new IllegalStateException("missing test resource " + VECTORS);
            }
            return json.readTree(in);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private JsonNode definitionWith(JsonNode policy) {
        ObjectNode definition = json.createObjectNode();
        definition.set("policy", policy);
        return definition;
    }

    @TestFactory
    List<DynamicTest> everyVectorFromTheGatewayCompilerMatches() {
        List<DynamicTest> tests = new ArrayList<>();
        vectors().properties().forEach(entry -> tests.add(DynamicTest.dynamicTest(entry.getKey(), () -> {
            JsonNode vector = entry.getValue();
            Optional<String> digest = AccessPolicyDigest.expected(definitionWith(vector.get("policy")));
            if (vector.get("digest").isNull()) {
                assertThat(digest).as("a policy the engine enforces on its own needs no digest").isEmpty();
            } else {
                assertThat(digest).contains(vector.get("digest").asString());
            }
        })));
        assertThat(tests).hasSizeGreaterThan(8);
        return tests;
    }

    @Test
    void aDefinitionWithoutAPolicyHasNoDigest() {
        assertThat(AccessPolicyDigest.expected(json.createObjectNode())).isEmpty();
        assertThat(AccessPolicyDigest.expected((JsonNode) null)).isEmpty();
    }

    @Test
    void anImpossibleLocalTimeIsRefusedRatherThanGuessed() {
        // 2026-03-08 02:30 在纽约不存在（夏令时拨快跳过），网关同样拒绝。
        JsonNode policy = json.readTree("""
                {"policyVersion":1,"window":{"opensAt":"2026-03-08T02:30","timezone":"America/New_York"}}
                """);

        assertThatThrownBy(() -> AccessPolicyDigest.expected(definitionWith(policy)))
                .isInstanceOf(AccessPolicyDigest.UncomputableDigestException.class);
    }

    @Test
    void anUnreadableIpRuleIsRefusedRatherThanGuessed() {
        JsonNode policy = json.readTree("""
                {"policyVersion":1,"network":{"denyIps":["10.0.0.0/255.255.255.0"],"regionUnknown":"deny"}}
                """);

        assertThatThrownBy(() -> AccessPolicyDigest.expected(definitionWith(policy)))
                .isInstanceOf(AccessPolicyDigest.UncomputableDigestException.class);
    }

    @Test
    void canonicalNetworksMatchPythonsIpaddress() {
        assertThat(CanonicalIpNetwork.canonical("10.0.0.5/8")).isEqualTo("10.0.0.0/8");
        assertThat(CanonicalIpNetwork.canonical("192.168.1.1")).isEqualTo("192.168.1.1/32");
        assertThat(CanonicalIpNetwork.canonical(" 172.16.30.200/20 ")).isEqualTo("172.16.16.0/20");
        assertThat(CanonicalIpNetwork.canonical("2001:0db8:0000:0000:0000:0000:0000:0001"))
                .isEqualTo("2001:db8::1/128");
        assertThat(CanonicalIpNetwork.canonical("2001:db8:abcd::/48")).isEqualTo("2001:db8:abcd::/48");
        assertThat(CanonicalIpNetwork.canonical("::1")).isEqualTo("::1/128");
        assertThat(CanonicalIpNetwork.canonical("::")).isEqualTo("::/128");
        assertThat(CanonicalIpNetwork.canonical("0:0:0:0:0:ffff:1.2.3.4/128")).isEqualTo("::ffff:102:304/128");
        assertThat(CanonicalIpNetwork.canonical("2001:db8::8a2e:370:7334")).isEqualTo("2001:db8::8a2e:370:7334/128");
        assertThat(CanonicalIpNetwork.canonical("2001:0:0:1:0:0:0:1")).isEqualTo("2001:0:0:1::1/128");
    }

    @Test
    void addressesThePlatformCannotReadAreRefused() {
        for (String value : List.of("010.0.0.1", "10.0.0.256", "10.0.0", "example.com", "", "10.0.0.0/33",
                "2001:db8::/129", "10.0.0.0/x")) {
            assertThatThrownBy(() -> CanonicalIpNetwork.canonical(value))
                    .as(value)
                    .isInstanceOf(CanonicalIpNetwork.MalformedNetworkException.class);
        }
    }
}
