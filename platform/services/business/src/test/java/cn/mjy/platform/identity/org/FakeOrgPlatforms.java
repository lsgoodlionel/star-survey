package cn.mjy.platform.identity.org;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 三家开放平台的本地替身（进程内 HTTP 服务），只实现平台实际调用的接口，报文字段与官方文档一致：
 *
 * <ul>
 *   <li>企业微信 /wecom：gettoken、auth/getuserinfo、user/get（错误码 40013/40001/40014/40029/60111）；</li>
 *   <li>钉钉 /dingtalk（api.dingtalk.com）：oauth2/userAccessToken、contact/users/me、oauth2/accessToken；
 *       /dingtalk-oapi（oapi.dingtalk.com）：topapi/user/getbyunionid、topapi/v2/user/get（60121）；</li>
 *   <li>飞书 /feishu：authen/v2/oauth/token（20002/20003/20004/20024）、authen/v1/user_info、
 *       auth/v3/tenant_access_token/internal、contact/v3/users/{id}。</li>
 * </ul>
 *
 * 授权码一次性、默认 5 分钟有效，与官方一致；测试可让授权码提前过期、让某个用户离职或被禁用、让下一次调用失败。
 * 全部状态在一个进程级单例里，测试之间用随机的企业与用户标识互相隔开。
 */
public final class FakeOrgPlatforms {

    public enum Status { ACTIVE, DISABLED, LEFT, DELETED }

    private record App(String provider, String corp, String appId, String secret) {
    }

    private record Code(String provider, String corp, String appId, String userId, Instant expiresAt) {
    }

    private record Token(String provider, String corp, String appId, String userId) {
    }

    private static final FakeOrgPlatforms INSTANCE = start();

    private final JsonMapper json = JsonMapper.builder().build();
    private final HttpServer server;
    private final Map<String, App> apps = new ConcurrentHashMap<>();
    private final Map<String, Code> codes = new ConcurrentHashMap<>();
    private final Map<String, Token> tokens = new ConcurrentHashMap<>();
    private final Map<String, Status> users = new ConcurrentHashMap<>();
    private final List<String> requestLog = new CopyOnWriteArrayList<>();
    private volatile int failNextWithStatus;

    private FakeOrgPlatforms(HttpServer server) {
        this.server = server;
    }

    public static FakeOrgPlatforms get() {
        return INSTANCE;
    }

    private static FakeOrgPlatforms start() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            FakeOrgPlatforms fake = new FakeOrgPlatforms(server);
            server.createContext("/", fake::handle);
            server.start();
            return fake;
        } catch (IOException e) {
            throw new IllegalStateException("cannot start fake org platforms", e);
        }
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    // ---------------------------------------------------------------- 测试操作

    /** 登记一个企业内部应用：企业微信 (corpid, agentid, secret)、钉钉 (corpId, AppKey, AppSecret)、飞书 (tenant_key, App ID, App Secret)。 */
    public void registerApp(String provider, String corp, String appId, String secret) {
        apps.put(appKey(provider, appId), new App(provider, corp, appId, secret));
    }

    public void addUser(String provider, String corp, String userId) {
        users.put(userKey(provider, corp, userId), Status.ACTIVE);
    }

    public void setStatus(String provider, String corp, String userId, Status status) {
        users.put(userKey(provider, corp, userId), status);
    }

    /** 用户在 corp 企业里对 appId 应用授权后，开放平台回调带回的授权码。 */
    public String issueCode(String provider, String corp, String appId, String userId) {
        String code = "code" + UUID.randomUUID().toString().replace("-", "");
        codes.put(code, new Code(provider, corp, appId, userId, Instant.now().plusSeconds(300)));
        return code;
    }

    public void expireCode(String code) {
        codes.computeIfPresent(code, (k, c) -> new Code(c.provider(), c.corp(), c.appId(), c.userId(),
                Instant.now().minusSeconds(1)));
    }

    /** 下一次请求直接返回该 HTTP 状态（模拟开放平台故障）。 */
    public void failNextWith(int httpStatus) {
        failNextWithStatus = httpStatus;
    }

    /** 收到过的全部请求（方法 + 路径 + 查询 + 请求体），用于断言平台发出去的内容。 */
    public List<String> requestLog() {
        return List.copyOf(requestLog);
    }

    // ---------------------------------------------------------------- 分发

    private void handle(HttpExchange exchange) throws IOException {
        try (exchange) {
            byte[] body = exchange.getRequestBody().readAllBytes();
            String path = exchange.getRequestURI().getPath();
            String rawQuery = exchange.getRequestURI().getRawQuery();
            requestLog.add(exchange.getRequestMethod() + " " + path + "?" + rawQuery + " "
                    + new String(body, StandardCharsets.UTF_8));
            int injected = failNextWithStatus;
            if (injected != 0) {
                failNextWithStatus = 0;
                reply(exchange, injected, "{\"message\":\"injected failure\"}");
                return;
            }
            Map<String, String> query = parseQuery(rawQuery);
            JsonNode request = body.length == 0 ? json.createObjectNode() : json.readTree(body);
            Reply reply = route(exchange, path, query, request);
            reply(exchange, reply.status(), json.writeValueAsString(reply.body()));
        }
    }

    private record Reply(int status, ObjectNode body) {
    }

    private Reply route(HttpExchange exchange, String path, Map<String, String> query, JsonNode request) {
        if (path.startsWith("/wecom/")) {
            return wecom(path.substring("/wecom".length()), query);
        }
        if (path.startsWith("/dingtalk/")) {
            return dingtalk(path.substring("/dingtalk".length()), exchange, request);
        }
        if (path.startsWith("/dingtalk-oapi/")) {
            return dingtalkOapi(path.substring("/dingtalk-oapi".length()), query, request);
        }
        if (path.startsWith("/feishu/")) {
            return feishu(path.substring("/feishu".length()), exchange, request);
        }
        return new Reply(404, obj().put("message", "no such endpoint"));
    }

    // ---------------------------------------------------------------- 企业微信

    private Reply wecom(String path, Map<String, String> query) {
        switch (path) {
            case "/cgi-bin/gettoken" -> {
                App app = apps.values().stream()
                        .filter(a -> a.provider().equals("wecom") && a.corp().equals(query.get("corpid")))
                        .findFirst().orElse(null);
                if (app == null) {
                    return wecomError(40013, "invalid corpid");
                }
                if (!app.secret().equals(query.get("corpsecret"))) {
                    return wecomError(40001, "invalid credential");
                }
                String token = newToken(new Token("wecom", app.corp(), app.appId(), null));
                return new Reply(200, obj().put("errcode", 0).put("errmsg", "ok")
                        .put("access_token", token).put("expires_in", 7200));
            }
            case "/cgi-bin/auth/getuserinfo" -> {
                Token token = tokens.get(String.valueOf(query.get("access_token")));
                if (token == null || !token.provider().equals("wecom")) {
                    return wecomError(40014, "invalid access_token");
                }
                Code code = redeem(query.get("code"));
                if (code == null || !code.provider().equals("wecom") || !code.corp().equals(token.corp())) {
                    return wecomError(40029, "invalid code");
                }
                return new Reply(200, obj().put("errcode", 0).put("errmsg", "ok").put("userid", code.userId()));
            }
            case "/cgi-bin/user/get" -> {
                Token token = tokens.get(String.valueOf(query.get("access_token")));
                if (token == null || !token.provider().equals("wecom")) {
                    return wecomError(40014, "invalid access_token");
                }
                Status status = users.get(userKey("wecom", token.corp(), query.get("userid")));
                if (status == null || status == Status.DELETED) {
                    return wecomError(60111, "userid not found");
                }
                int code = switch (status) {
                    case ACTIVE -> 1;
                    case DISABLED -> 2;
                    default -> 5;
                };
                return new Reply(200, obj().put("errcode", 0).put("errmsg", "ok")
                        .put("userid", query.get("userid")).put("status", code));
            }
            default -> {
                return new Reply(404, obj().put("errcode", 404).put("errmsg", "no such api"));
            }
        }
    }

    private Reply wecomError(int code, String message) {
        return new Reply(200, obj().put("errcode", code).put("errmsg", message));
    }

    // ---------------------------------------------------------------- 钉钉

    private Reply dingtalk(String path, HttpExchange exchange, JsonNode request) {
        switch (path) {
            case "/v1.0/oauth2/userAccessToken" -> {
                App app = apps.get(appKey("dingtalk", text(request, "clientId")));
                if (app == null || !app.secret().equals(text(request, "clientSecret"))) {
                    return dingtalkError(400, "invalidClientIdOrSecret", "无效的clientId或者clientSecret");
                }
                if (!"authorization_code".equals(text(request, "grantType"))) {
                    return dingtalkError(400, "invalidGrantType", "grantType error");
                }
                Code code = redeem(text(request, "code"));
                if (code == null || !code.provider().equals("dingtalk") || !code.appId().equals(app.appId())) {
                    return dingtalkError(400, "invalidAuthCode", "不合法的临时授权码");
                }
                String token = newToken(new Token("dingtalk", code.corp(), app.appId(), code.userId()));
                return new Reply(200, obj().put("accessToken", token).put("refreshToken", "r" + token)
                        .put("expireIn", 7200).put("corpId", code.corp()));
            }
            case "/v1.0/contact/users/me" -> {
                Token token = tokens.get(String.valueOf(exchange.getRequestHeaders().getFirst("x-acs-dingtalk-access-token")));
                if (token == null || token.userId() == null) {
                    return dingtalkError(401, "InvalidAuthentication", "不合法的access_token");
                }
                return new Reply(200, obj().put("nick", "用户").put("unionId", unionId(token.corp(), token.userId()))
                        .put("openId", "open-" + token.appId() + "-" + token.userId()));
            }
            case "/v1.0/oauth2/accessToken" -> {
                App app = apps.get(appKey("dingtalk", text(request, "appKey")));
                if (app == null || !app.secret().equals(text(request, "appSecret"))) {
                    return dingtalkError(400, "invalidClientIdOrSecret", "无效的appKey或者appSecret");
                }
                String token = newToken(new Token("dingtalk", app.corp(), app.appId(), null));
                return new Reply(200, obj().put("accessToken", token).put("expireIn", 7200));
            }
            default -> {
                return dingtalkError(404, "notFound", "no such api");
            }
        }
    }

    private Reply dingtalkOapi(String path, Map<String, String> query, JsonNode request) {
        Token token = tokens.get(String.valueOf(query.get("access_token")));
        if (token == null || !token.provider().equals("dingtalk") || token.userId() != null) {
            return new Reply(200, obj().put("errcode", 88).put("errmsg", "不合法的access_token"));
        }
        switch (path) {
            case "/topapi/user/getbyunionid" -> {
                String union = text(request, "unionid");
                String userId = users.keySet().stream()
                        .filter(k -> k.startsWith("dingtalk|" + token.corp() + "|"))
                        .map(k -> k.substring(("dingtalk|" + token.corp() + "|").length()))
                        .filter(u -> unionId(token.corp(), u).equals(union))
                        .findFirst().orElse(null);
                if (userId == null || users.get(userKey("dingtalk", token.corp(), userId)) == Status.DELETED) {
                    return new Reply(200, obj().put("errcode", 60121).put("errmsg", "找不到该用户"));
                }
                ObjectNode result = obj().put("contact_type", 0).put("userid", userId);
                return new Reply(200, obj().put("errcode", 0).put("errmsg", "ok").set("result", result));
            }
            case "/topapi/v2/user/get" -> {
                String userId = text(request, "userid");
                Status status = users.get(userKey("dingtalk", token.corp(), userId));
                if (status == null || status == Status.DELETED || status == Status.LEFT) {
                    return new Reply(200, obj().put("errcode", 60121).put("errmsg", "找不到该用户"));
                }
                ObjectNode result = obj().put("userid", userId).put("active", status == Status.ACTIVE);
                return new Reply(200, obj().put("errcode", 0).put("errmsg", "ok").set("result", result));
            }
            default -> {
                return new Reply(404, obj().put("errcode", 404).put("errmsg", "no such api"));
            }
        }
    }

    private Reply dingtalkError(int status, String code, String message) {
        return new Reply(status, obj().put("code", code).put("message", message).put("requestid", UUID.randomUUID().toString()));
    }

    // ---------------------------------------------------------------- 飞书

    private Reply feishu(String path, HttpExchange exchange, JsonNode request) {
        if (path.equals("/open-apis/authen/v2/oauth/token")) {
            App app = apps.get(appKey("feishu", text(request, "client_id")));
            if (app == null || !app.secret().equals(text(request, "client_secret"))) {
                return feishuOauthError(20002, "invalid_client");
            }
            Code code = codes.get(String.valueOf(text(request, "code")));
            if (code == null) {
                return feishuOauthError(20003, "invalid_grant");
            }
            if (code.expiresAt().isBefore(Instant.now())) {
                codes.remove(text(request, "code"));
                return feishuOauthError(20004, "invalid_grant");
            }
            if (!code.provider().equals("feishu") || !code.appId().equals(app.appId())) {
                return feishuOauthError(20024, "invalid_grant");
            }
            codes.remove(text(request, "code"));
            String token = newToken(new Token("feishu", code.corp(), app.appId(), code.userId()));
            return new Reply(200, obj().put("code", 0).put("access_token", token).put("expires_in", 7200)
                    .put("token_type", "Bearer").put("scope", "contact:user.employee_id:readonly"));
        }
        if (path.equals("/open-apis/auth/v3/tenant_access_token/internal")) {
            App app = apps.get(appKey("feishu", text(request, "app_id")));
            if (app == null || !app.secret().equals(text(request, "app_secret"))) {
                return new Reply(400, obj().put("code", 10014).put("msg", "app secret invalid"));
            }
            String token = newToken(new Token("feishu", app.corp(), app.appId(), null));
            return new Reply(200, obj().put("code", 0).put("msg", "ok").put("tenant_access_token", token).put("expire", 7200));
        }
        Token token = bearer(exchange);
        if (token == null || !token.provider().equals("feishu")) {
            return new Reply(401, obj().put("code", 20005).put("msg", "invalid access token"));
        }
        if (path.equals("/open-apis/authen/v1/user_info") && token.userId() != null) {
            ObjectNode data = obj().put("name", "用户").put("open_id", "ou_" + token.appId() + "_" + token.userId())
                    .put("union_id", "on_" + token.userId()).put("user_id", token.userId()).put("tenant_key", token.corp());
            return new Reply(200, obj().put("code", 0).put("msg", "success").set("data", data));
        }
        if (path.startsWith("/open-apis/contact/v3/users/") && token.userId() == null) {
            String userId = path.substring("/open-apis/contact/v3/users/".length());
            Status status = users.get(userKey("feishu", token.corp(), userId));
            if (status == null || status == Status.DELETED) {
                return new Reply(400, obj().put("code", 41050).put("msg", "no user authority error"));
            }
            ObjectNode userStatus = obj().put("is_frozen", status == Status.DISABLED)
                    .put("is_resigned", status == Status.LEFT).put("is_activated", true);
            ObjectNode user = obj().put("user_id", userId);
            user.set("status", userStatus);
            ObjectNode data = obj();
            data.set("user", user);
            return new Reply(200, obj().put("code", 0).put("msg", "success").set("data", data));
        }
        return new Reply(404, obj().put("code", 404).put("msg", "no such api"));
    }

    private Reply feishuOauthError(int code, String error) {
        return new Reply(400, obj().put("code", code).put("error", error).put("error_description", "fake " + code));
    }

    private Token bearer(HttpExchange exchange) {
        String header = exchange.getRequestHeaders().getFirst("Authorization");
        if (header == null || !header.startsWith("Bearer ")) {
            return null;
        }
        return tokens.get(header.substring("Bearer ".length()));
    }

    // ---------------------------------------------------------------- 工具

    /** 一次性兑换：取出即作废；过期的同样作废并返回空。 */
    private Code redeem(String value) {
        if (value == null) {
            return null;
        }
        Code code = codes.remove(value);
        return code == null || code.expiresAt().isBefore(Instant.now()) ? null : code;
    }

    private String newToken(Token token) {
        String value = "tok" + UUID.randomUUID().toString().replace("-", "");
        tokens.put(value, token);
        return value;
    }

    private static String unionId(String corp, String userId) {
        return "union-" + Integer.toHexString((corp + "|" + userId).hashCode());
    }

    private static String appKey(String provider, String appId) {
        return provider + "|" + appId;
    }

    private static String userKey(String provider, String corp, String userId) {
        return provider + "|" + corp + "|" + userId;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value == null || value.isNull() ? null : value.asString();
    }

    private ObjectNode obj() {
        return json.createObjectNode();
    }

    private static Map<String, String> parseQuery(String raw) {
        Map<String, String> result = new HashMap<>();
        if (raw == null || raw.isEmpty()) {
            return result;
        }
        for (String pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            String key = URLDecoder.decode(eq < 0 ? pair : pair.substring(0, eq), StandardCharsets.UTF_8);
            String value = eq < 0 ? "" : URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
            result.put(key, value);
        }
        return result;
    }

    private static void reply(HttpExchange exchange, int status, String body) throws IOException {
        byte[] out = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, out.length);
        try (OutputStream stream = exchange.getResponseBody()) {
            stream.write(out);
        }
    }
}
