package cn.mjy.platform.survey.gateway;

import java.util.ArrayList;
import java.util.List;
import tools.jackson.databind.JsonNode;

/**
 * 把网关应答体解析成值对象。网关是外部系统：缺字段、类型不对一律抛 {@link MalformedResponseException}，
 * 由调用方归为"结果未知"，绝不带着半成品往下走。
 */
final class GatewayResponseParser {

    private GatewayResponseParser() {
    }

    static final class MalformedResponseException extends RuntimeException {

        MalformedResponseException(String message) {
            super(message);
        }
    }

    /** 200/422/502 应答体里的 {@code result}；requireBinding 为真时（200）binding 必须完整。 */
    static GatewayResult result(JsonNode body, boolean requireBinding) {
        JsonNode result = object(body, "result");
        JsonNode binding = result.get("binding");
        boolean hasBinding = binding != null && !binding.isNull();
        if (requireBinding && !hasBinding) {
            throw new MalformedResponseException("published response carries no binding");
        }
        return new GatewayResult(
                bool(result, "ok"),
                optionalInt(result, "surveyId"),
                optionalText(result, "failedStage"),
                texts(result, "failures"),
                bool(result, "rolledBack"),
                optionalInt(result, "orphanSurveyId"),
                hasBinding ? binding(binding) : null,
                optionalText(result, "policyDigest"),
                invitations(result));
    }

    /**
     * 回执里的邀请码（契约 v1.2）。没有这个键就是"这次发布不带参与者"；有就必须条条完整——
     * 少一个 token 就意味着某个人拿不到码，宁可整份应答判为坏掉，也不要发出打不开的邀请。
     */
    private static List<GatewayInvitation> invitations(JsonNode result) {
        JsonNode node = result.get("invitations");
        if (node == null || node.isNull()) {
            return List.of();
        }
        if (!node.isArray()) {
            throw new MalformedResponseException("not an array: invitations");
        }
        List<GatewayInvitation> invitations = new ArrayList<>();
        for (JsonNode item : node) {
            invitations.add(new GatewayInvitation(
                    requiredInt(item, "index"), optionalText(item, "ref"), text(item, "token")));
        }
        return List.copyOf(invitations);
    }

    /**
     * 410 应答体里的 {@code expired} 块。这里<b>刻意宽松</b>：410 已经说明了要紧的那件事
     * （回执过期、重发没有意义），细节读不出来也不能退回"结果未知"——那是一个永远走不完的重试环。
     */
    static GatewayOutcome.Expired expired(JsonNode body) {
        JsonNode expired = body == null ? null : body.get("expired");
        if (expired == null || !expired.isObject()) {
            return new GatewayOutcome.Expired(0, null, null);
        }
        return new GatewayOutcome.Expired(lenientInt(expired, "originalStatus", 0),
                boxedLenientInt(expired, "surveyId"), lenientText(expired, "createdAt"));
    }

    private static int lenientInt(JsonNode node, String name, int fallback) {
        Integer value = boxedLenientInt(node, name);
        return value == null ? fallback : value;
    }

    private static Integer boxedLenientInt(JsonNode node, String name) {
        JsonNode value = node.get(name);
        return value != null && value.isIntegralNumber() && value.canConvertToInt() ? value.intValue() : null;
    }

    private static String lenientText(JsonNode node, String name) {
        JsonNode value = node.get(name);
        return value != null && value.isString() ? value.asString() : null;
    }

    /** 400/401/404 应答体里的 {@code error}；缺失时退回 HTTP 状态说明。 */
    static String error(JsonNode body, int status) {
        JsonNode error = body == null ? null : body.get("error");
        return error != null && error.isString() ? error.asString() : "http_" + status;
    }

    private static GatewayBinding binding(JsonNode node) {
        List<GatewayBinding.QuestionBinding> questions = new ArrayList<>();
        for (JsonNode question : array(node, "questions")) {
            questions.add(new GatewayBinding.QuestionBinding(
                    text(question, "uuid"), text(question, "code"), text(question, "type"), fields(question)));
        }
        return new GatewayBinding(
                text(node, "engineInstance"),
                requiredInt(node, "surveyId"),
                text(node, "definitionUuid"),
                text(node, "compilerVersion"),
                text(node, "fingerprintVersion"),
                text(node, "fingerprint"),
                text(node, "language"),
                text(node, "publishedAt"),
                questions);
    }

    private static List<GatewayBinding.FieldBinding> fields(JsonNode question) {
        List<GatewayBinding.FieldBinding> fields = new ArrayList<>();
        for (JsonNode field : array(question, "fields")) {
            JsonNode aid = field.get("aid");
            fields.add(new GatewayBinding.FieldBinding(
                    text(field, "fieldname"),
                    aid == null || aid.isNull() ? "" : aid.asString(),
                    requiredInt(field, "scale")));
        }
        return fields;
    }

    private static JsonNode object(JsonNode node, String name) {
        JsonNode value = node == null ? null : node.get(name);
        if (value == null || !value.isObject()) {
            throw new MalformedResponseException("missing object: " + name);
        }
        return value;
    }

    private static JsonNode array(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || !value.isArray()) {
            throw new MalformedResponseException("missing array: " + name);
        }
        return value;
    }

    private static String text(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || !value.isString() || value.asString().isBlank()) {
            throw new MalformedResponseException("missing text: " + name);
        }
        return value.asString();
    }

    private static String optionalText(JsonNode node, String name) {
        JsonNode value = node.get(name);
        return value == null || value.isNull() ? null : value.asString();
    }

    private static int requiredInt(JsonNode node, String name) {
        Integer value = optionalInt(node, name);
        if (value == null) {
            throw new MalformedResponseException("missing integer: " + name);
        }
        return value;
    }

    private static Integer optionalInt(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isIntegralNumber() || !value.canConvertToInt()) {
            throw new MalformedResponseException("not an integer: " + name);
        }
        return value.intValue();
    }

    private static boolean bool(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || !value.isBoolean()) {
            throw new MalformedResponseException("missing boolean: " + name);
        }
        return value.booleanValue();
    }

    private static List<String> texts(JsonNode node, String name) {
        List<String> values = new ArrayList<>();
        JsonNode array = node.get(name);
        if (array == null || array.isNull()) {
            return values;
        }
        if (!array.isArray()) {
            throw new MalformedResponseException("not an array: " + name);
        }
        for (JsonNode item : array) {
            values.add(item.asString());
        }
        return values;
    }
}
