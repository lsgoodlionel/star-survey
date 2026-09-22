package cn.mjy.platform.survey.gateway;

import cn.mjy.platform.survey.gateway.GatewayResponseParser.MalformedResponseException;
import java.util.ArrayList;
import java.util.List;
import tools.jackson.databind.JsonNode;

/**
 * 契约 v1.2 两个接口的 200 应答解析。与 {@link GatewayResponseParser} 一样：缺字段、类型不对一律抛
 * {@link MalformedResponseException}，由调用方归为"结论未知"。
 */
final class GatewayOperationsParser {

    private GatewayOperationsParser() {
    }

    /** {@code {"status":"closed","result":{"surveyId":…,"expires":"…","alreadyClosed":…}}}，且 sid 须与请求一致。 */
    static CloseOutcome.Closed closed(JsonNode body, int expectedSid) {
        requireStatus(body, "closed");
        JsonNode result = object(body, "result");
        if (integer(result, "surveyId") != expectedSid) {
            throw new MalformedResponseException("close result names another survey");
        }
        return new CloseOutcome.Closed(text(result, "expires"), bool(result, "alreadyClosed"));
    }

    /** {@code {"status":"match"|"drift","result":<DriftResult>}}；status 与 drifted 必须一致。 */
    static DriftOutcome.Checked checked(JsonNode body, int expectedSid) {
        JsonNode status = body == null ? null : body.get("status");
        if (status == null || !status.isString()
                || !("match".equals(status.asString()) || "drift".equals(status.asString()))) {
            throw new MalformedResponseException("drift-check status is neither match nor drift");
        }
        JsonNode result = object(body, "result");
        boolean drifted = bool(result, "drifted");
        if (drifted != "drift".equals(status.asString()) || integer(result, "surveyId") != expectedSid) {
            throw new MalformedResponseException("drift-check result contradicts itself or names another survey");
        }
        List<DriftOutcome.Issue> issues = new ArrayList<>();
        for (JsonNode issue : array(result, "issues")) {
            issues.add(new DriftOutcome.Issue(text(issue, "code"), optionalText(issue, "detail")));
        }
        List<DriftOutcome.Rename> renamed = new ArrayList<>();
        for (JsonNode rename : array(result, "renamed")) {
            renamed.add(new DriftOutcome.Rename(text(rename, "uuid"), text(rename, "from"), text(rename, "to")));
        }
        if (drifted && issues.isEmpty()) {
            throw new MalformedResponseException("drift reported without any issue");
        }
        return new DriftOutcome.Checked(drifted, optionalText(result, "currentFingerprint"),
                optionalText(result, "active"), issues, renamed);
    }

    private static void requireStatus(JsonNode body, String expected) {
        JsonNode status = body == null ? null : body.get("status");
        if (status == null || !status.isString() || !expected.equals(status.asString())) {
            throw new MalformedResponseException("status is not " + expected);
        }
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

    private static int integer(JsonNode node, String name) {
        JsonNode value = node.get(name);
        if (value == null || !value.isIntegralNumber() || !value.canConvertToInt()) {
            throw new MalformedResponseException("missing integer: " + name);
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
}
