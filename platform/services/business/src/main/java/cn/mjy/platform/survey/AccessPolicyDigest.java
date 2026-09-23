package cn.mjy.platform.survey;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 平台自己算一遍网关那份「插件载荷」的 SHA-256，用来核对发布回执里的 {@code policyDigest}（ADR 0016）。
 *
 * <p>载荷的定义在契约 survey-access-policy-v1 §4，实现在网关 {@code pubgw/policy/compile.py::_payload}：
 * 键排序、紧凑、纯 ASCII 的 JSON。这里刻意逐字复刻，包括
 * <ul>
 *   <li>本地时刻按 IANA 时区换成 UTC，写成引擎格式 {@code yyyy-MM-dd HH:mm:ss}；</li>
 *   <li>限次按 token → device → ip 排序；</li>
 *   <li>IP 规则写成规范 CIDR（见 {@link CanonicalIpNetwork}）；</li>
 *   <li>只有验证码／邀请码的策略不需要插件，也就没有摘要。</li>
 * </ul>
 * 两端的一致性由同一张向量表钉住：{@code src/test/resources/policy/digest-vectors.json}
 * 同时被 {@code AccessPolicyDigestTest} 与网关的 {@code tests/test_policy_digest_vectors.py} 读取。
 *
 * <p>算不出来（时刻在夏令时缺口里、IP 写法看不懂）一律抛 {@link UncomputableDigestException}：
 * 这些定义网关本该在校验阶段就拒掉，收到"已发布"却算不出摘要，只能当作没被执行。纯函数。
 */
final class AccessPolicyDigest {

    static final String SCHEMA = "mjy-access-policy/1";
    /** 按可靠程度排序，与网关 {@code IDENTITIES} 一致。 */
    private static final List<String> IDENTITIES = List.of("token", "device", "ip");
    private static final String DEFAULT_REGION_UNKNOWN = "deny";
    private static final DateTimeFormatter ENGINE_DATETIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final Pattern LOCAL = Pattern.compile("\\A(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2})(?::(\\d{2}))?\\z");

    /** 只读地解析已固化的定义快照，不参与任何业务配置。 */
    private static final JsonMapper MAPPER = JsonMapper.builder().build();

    private AccessPolicyDigest() {
    }

    static final class UncomputableDigestException extends RuntimeException {

        UncomputableDigestException(String message) {
            super(message);
        }
    }

    /** 发给网关的那份定义快照（JSON 文本）。 */
    static Optional<String> expected(String definitionJson) {
        try {
            return expected(MAPPER.readTree(definitionJson));
        } catch (RuntimeException e) {
            throw new UncomputableDigestException("the published definition snapshot is not readable JSON");
        }
    }

    /** 定义里没有需要插件执行的策略时返回空：网关这时不回摘要。 */
    static Optional<String> expected(JsonNode definition) {
        JsonNode policy = definition == null ? null : definition.get("policy");
        if (policy == null || !policy.isObject()) {
            return Optional.empty();
        }
        JsonNode window = block(policy, "window");
        JsonNode access = block(policy, "access");
        JsonNode limits = block(policy, "limits");
        JsonNode network = block(policy, "network");
        String passwordHash = text(access, "passwordHash");
        List<JsonNode> responses = responses(limits);
        Integer duration = integer(limits, "maxDurationSeconds");
        if (window == null && passwordHash == null && responses.isEmpty() && duration == null && network == null) {
            return Optional.empty();
        }
        return Optional.of(sha256(payload(window, passwordHash, responses, duration, network)));
    }

    /** 键按字母序，与 {@code json.dumps(..., sort_keys=True, separators=(",", ":"))} 一致。 */
    private static String payload(JsonNode window, String passwordHash, List<JsonNode> responses,
            Integer duration, JsonNode network) {
        return "{"
                + "\"maxDurationSeconds\":" + (duration == null ? "null" : duration.toString())
                + ",\"network\":" + network(network)
                + ",\"passwordHash\":" + string(passwordHash)
                + ",\"responses\":" + responses(responses)
                + ",\"schema\":" + string(SCHEMA)
                + ",\"window\":" + window(window)
                + "}";
    }

    private static String window(JsonNode window) {
        if (window == null) {
            return "null";
        }
        String zone = text(window, "timezone");
        String opensLocal = text(window, "opensAt");
        String closesLocal = text(window, "closesAt");
        return "{"
                + "\"closesAt\":" + string(engineUtc(closesLocal, zone))
                + ",\"closesAtLocal\":" + string(closesLocal)
                + ",\"opensAt\":" + string(engineUtc(opensLocal, zone))
                + ",\"opensAtLocal\":" + string(opensLocal)
                + ",\"timezone\":" + string(zone)
                + "}";
    }

    private static String network(JsonNode network) {
        if (network == null) {
            return "null";
        }
        String regionUnknown = text(network, "regionUnknown");
        return "{"
                + "\"allowIps\":" + networks(network, "allowIps")
                + ",\"allowRegions\":" + strings(network, "allowRegions")
                + ",\"denyIps\":" + networks(network, "denyIps")
                + ",\"denyRegions\":" + strings(network, "denyRegions")
                + ",\"regionUnknown\":" + string(regionUnknown == null ? DEFAULT_REGION_UNKNOWN : regionUnknown)
                + "}";
    }

    private static String responses(List<JsonNode> entries) {
        StringBuilder json = new StringBuilder("[");
        for (JsonNode entry : entries) {
            json.append(json.length() > 1 ? "," : "")
                    .append("{\"by\":").append(string(text(entry, "by")))
                    .append(",\"max\":").append(integer(entry, "max")).append("}");
        }
        return json.append("]").toString();
    }

    /** 按 token → device → ip 排序；出现别的维度说明这份定义本不该通过校验。 */
    private static List<JsonNode> responses(JsonNode limits) {
        JsonNode entries = limits == null ? null : limits.get("responses");
        if (entries == null || !entries.isArray()) {
            return List.of();
        }
        List<JsonNode> sorted = new ArrayList<>();
        entries.forEach(sorted::add);
        sorted.sort((left, right) -> Integer.compare(identity(left), identity(right)));
        return List.copyOf(sorted);
    }

    private static int identity(JsonNode entry) {
        int index = IDENTITIES.indexOf(text(entry, "by"));
        if (index < 0) {
            throw new UncomputableDigestException("unknown response limit identity");
        }
        return index;
    }

    private static String networks(JsonNode network, String name) {
        StringBuilder json = new StringBuilder("[");
        for (String value : list(network, name)) {
            try {
                json.append(json.length() > 1 ? "," : "").append(string(CanonicalIpNetwork.canonical(value)));
            } catch (CanonicalIpNetwork.MalformedNetworkException e) {
                throw new UncomputableDigestException("policy.network." + name + ": " + e.getMessage());
            }
        }
        return json.append("]").toString();
    }

    private static String strings(JsonNode network, String name) {
        StringBuilder json = new StringBuilder("[");
        for (String value : list(network, name)) {
            json.append(json.length() > 1 ? "," : "").append(string(value));
        }
        return json.append("]").toString();
    }

    private static List<String> list(JsonNode node, String name) {
        JsonNode array = node.get(name);
        if (array == null || !array.isArray()) {
            return List.of();
        }
        List<String> values = new ArrayList<>();
        for (JsonNode item : array) {
            if (!item.isString()) {
                throw new UncomputableDigestException("policy.network." + name + " holds a non-string");
            }
            values.add(item.asString());
        }
        return values;
    }

    /** 本地时刻 → 引擎格式的 UTC 文本；夏令时缺口与重叠不猜（网关同样拒绝）。 */
    private static String engineUtc(String local, String zoneName) {
        if (local == null) {
            return null;
        }
        Matcher match = LOCAL.matcher(local);
        if (match.matches() && zoneName != null) {
            LocalDateTime moment = local(match);
            ZoneId zone = zone(zoneName);
            if (zone.getRules().getValidOffsets(moment).size() != 1) {
                throw new UncomputableDigestException("local time does not exist or is ambiguous in " + zoneName);
            }
            return ZonedDateTime.of(moment, zone).withZoneSameInstant(ZoneOffset.UTC).format(ENGINE_DATETIME);
        }
        throw new UncomputableDigestException("policy.window holds a time or zone the platform cannot read");
    }

    private static LocalDateTime local(Matcher match) {
        try {
            return LocalDateTime.of(
                    Integer.parseInt(match.group(1)), Integer.parseInt(match.group(2)),
                    Integer.parseInt(match.group(3)), Integer.parseInt(match.group(4)),
                    Integer.parseInt(match.group(5)),
                    match.group(6) == null ? 0 : Integer.parseInt(match.group(6)));
        } catch (RuntimeException e) {
            throw new UncomputableDigestException("policy.window holds an impossible local time");
        }
    }

    private static ZoneId zone(String name) {
        try {
            return ZoneId.of(name);
        } catch (RuntimeException e) {
            throw new UncomputableDigestException("policy.window.timezone is not an IANA zone");
        }
    }

    private static JsonNode block(JsonNode policy, String name) {
        JsonNode value = policy.get(name);
        return value != null && value.isObject() ? value : null;
    }

    private static String text(JsonNode node, String name) {
        JsonNode value = node == null ? null : node.get(name);
        return value == null || !value.isString() ? null : value.asString();
    }

    private static Integer integer(JsonNode node, String name) {
        JsonNode value = node == null ? null : node.get(name);
        return value == null || !value.isIntegralNumber() ? null : value.intValue();
    }

    /** {@code ensure_ascii=True} 的转义：控制字符与非 ASCII 都写成 {@code \\uXXXX}，{@code /} 不转义。 */
    private static String string(String value) {
        if (value == null) {
            return "null";
        }
        StringBuilder json = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> json.append("\\\"");
                case '\\' -> json.append("\\\\");
                case '\n' -> json.append("\\n");
                case '\r' -> json.append("\\r");
                case '\t' -> json.append("\\t");
                case '\b' -> json.append("\\b");
                case '\f' -> json.append("\\f");
                default -> json.append(c >= 0x20 && c < 0x7F ? String.valueOf(c) : String.format("\\u%04x", (int) c));
            }
        }
        return json.append("\"").toString();
    }

    private static String sha256(String payload) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(payload.getBytes(StandardCharsets.US_ASCII)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required", e);
        }
    }
}
