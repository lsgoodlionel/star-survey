package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.hasItem;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import com.jayway.jsonpath.JsonPath;
import jakarta.servlet.http.Cookie;
import java.lang.reflect.Constructor;
import java.lang.reflect.RecordComponent;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class OrgLoginHandoffTest extends OrgLoginTestSupport {

    private static final String HANDOFF_COOKIE = "mjy_org_handoff";

    @Autowired
    ApplicationContext applicationContext;

    @Autowired
    OrgLoginProperties loginProperties;

    record AdminCallback(String handoff, Cookie browser, URI location) {
    }

    @Test
    void adminWebCallbackRedirectsWithoutPuttingTheJwtInTheUrl() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);
        String userId = unique("u");
        String principal = preauthorize(org, userId);

        Started started = startAdminWeb(tenant, org.connectionId());
        AdminCallback callback = adminCallback(org, userId, started);

        Map<String, String> callbackQuery = query(callback.location());
        assertThat(callback.location().toString()).startsWith(ADMIN_WEB_BASE + "/auth/callback?");
        assertThat(callbackQuery).containsOnlyKeys("tenant", "handoff")
                .containsEntry("tenant", tenant.toString());
        assertThat(callback.handoff()).matches("[A-Za-z0-9_-]{43,}");
        assertThat(callback.location().toString()).doesNotContain("accessToken", "Bearer", principal);
        assertThat(sessionCount(tenant)).isZero();

        assertThat(callback.browser()).isNotNull();
        assertThat(callback.browser().isHttpOnly()).isTrue();
        assertThat(callback.browser().getSecure()).isTrue();
        assertThat(callback.browser().getPath()).isEqualTo(handoffPath(tenant));
    }

    @Test
    void exchangingTheHandoffCreatesTheSessionAndReturnsTheJwt() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "dingtalk", null);
        String userId = unique("u");
        FAKE.addUser(org.provider(), org.corp(), userId);
        String principal = preauthorize(org, userId);
        AdminCallback callback = adminCallback(org, userId, startAdminWeb(tenant, org.connectionId()));
        assertThat(sessionCount(tenant)).isZero();

        String body = exchange(tenant, callback.handoff(), callback.browser())
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.tokenType").value("Bearer"))
                .andExpect(jsonPath("$.principalId").value(principal))
                .andExpect(jsonPath("$.tenantId").value(tenant.toString()))
                .andReturn().getResponse().getContentAsString();

        String token = JsonPath.read(body, "$.accessToken");
        assertThat(token).isNotBlank();
        assertThat(sessionCount(tenant)).isEqualTo(1);
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.actorId").value(principal));
    }

    @Test
    void aHandoffCanOnlyBeUsedOnce() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", "jit_staff");
        String userId = unique("u");
        AdminCallback callback = adminCallback(org, userId, startAdminWeb(tenant, org.connectionId()));

        exchange(tenant, callback.handoff(), callback.browser()).andExpect(status().isOk());
        exchange(tenant, callback.handoff(), callback.browser())
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(OrgLoginException.INVALID_STATE));
        assertThat(sessionCount(tenant)).isEqualTo(1);
    }

    @Test
    void anExpiredHandoffIsRejectedWithoutCreatingASession() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);
        String principal = preauthorize(org, unique("u"));
        String handoff = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString(UUID.randomUUID().toString().replace("-", "").getBytes(StandardCharsets.US_ASCII));
        String browser = java.util.Base64.getUrlEncoder().withoutPadding()
                .encodeToString(UUID.randomUUID().toString().replace("-", "").getBytes(StandardCharsets.US_ASCII));
        tenantScope.run(tenant, () -> jdbc.sql("""
                        INSERT INTO org_login_handoff
                            (handoff_hash, browser_hash, tenant_id, principal_id, connection_id, provider, expires_at)
                        VALUES (:handoff, :browser, :tenant, :principal, :connection, :provider,
                                now() - interval '1 second')
                        """)
                .param("handoff", sha256(handoff))
                .param("browser", sha256(browser))
                .param("tenant", tenant.value())
                .param("principal", UUID.fromString(principal))
                .param("connection", org.connectionId())
                .param("provider", org.provider())
                .update());

        exchange(tenant, handoff, cookie(browser))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(OrgLoginException.INVALID_STATE));
        assertThat(sessionCount(tenant)).isZero();
    }

    @Test
    void aHandoffFromAnotherBrowserIsRejected() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", "jit_staff");
        AdminCallback callback = adminCallback(org, unique("u"), startAdminWeb(tenant, org.connectionId()));

        exchange(tenant, callback.handoff(), cookie("x".repeat(43)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(OrgLoginException.INVALID_STATE));
        assertThat(sessionCount(tenant)).isZero();
    }

    @Test
    void aHandoffCannotBeReadFromAnotherTenantScope() throws Exception {
        TenantId tenantA = newTenant();
        TenantId tenantB = newTenant();
        Org org = connect(tenantA, "feishu", "jit_staff");
        AdminCallback callback = adminCallback(org, unique("u"), startAdminWeb(tenantA, org.connectionId()));

        exchange(tenantB, callback.handoff(), callback.browser())
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(OrgLoginException.INVALID_STATE));
        assertThat(sessionCount(tenantA)).isZero();
        assertThat(sessionCount(tenantB)).isZero();
    }

    @Test
    void anUnconfiguredAdminWebRedirectIsRejectedAtStart() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);
        MockMvc unconfigured = MockMvcBuilders.standaloneSetup(controllerWithAdminWebBaseUrl(""))
                .setControllerAdvice(applicationContext.getBean(OrgLoginErrorHandler.class))
                .build();

        unconfigured.perform(get(startPath(tenant, org.connectionId())).param("client", "admin_web"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value(OrgLoginException.NOT_CONFIGURED));
    }

    @Test
    void anInsecureRemoteAdminWebBaseUrlIsRejected() {
        assertThatThrownBy(() -> copyProperties("http://admin.example.test"))
                .rootCause().isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("admin-web-base-url");
    }

    @Test
    void aLoopbackHttpAdminWebBaseUrlIsAllowedForTests() {
        assertThatCode(() -> copyProperties("http://127.0.0.1:3000"))
                .doesNotThrowAnyException();
    }

    private Started startAdminWeb(TenantId tenant, UUID connectionId) throws Exception {
        MvcResult result = mvc.perform(get(startPath(tenant, connectionId)).param("client", "admin_web"))
                .andExpect(status().isFound())
                .andReturn();
        URI location = URI.create(result.getResponse().getHeader("Location"));
        Cookie browser = result.getResponse().getCookie(OrgLoginController.BROWSER_COOKIE);
        assertThat(query(location).get("redirect_uri"))
                .isEqualTo(CALLBACK_BASE + callbackPath(tenant, connectionId) + "?client=admin_web");
        return new Started(query(location).get("state"), browser, location);
    }

    private AdminCallback adminCallback(Org org, String userId, Started started) throws Exception {
        String code = FAKE.issueCode(org.provider(), org.corp(), org.appId(), userId);
        MvcResult result = mvc.perform(get(callbackPath(org.tenant(), org.connectionId()))
                        .param("client", "admin_web")
                        .param("code", code)
                        .param("state", started.state())
                        .cookie(started.browser()))
                .andExpect(status().isFound())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(header().stringValues("Set-Cookie", hasItem(containsString("SameSite=Strict"))))
                .andReturn();
        URI location = URI.create(result.getResponse().getHeader("Location"));
        return new AdminCallback(query(location).get("handoff"),
                result.getResponse().getCookie(HANDOFF_COOKIE), location);
    }

    private ResultActions exchange(TenantId tenant, String handoff, Cookie browser) throws Exception {
        return mvc.perform(post(handoffPath(tenant))
                .cookie(browser)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"handoff\":\"" + handoff + "\"}"));
    }

    private long sessionCount(TenantId tenant) {
        return tenantScope.call(tenant,
                () -> jdbc.sql("SELECT COUNT(*) FROM org_login_session").query(Long.class).single());
    }

    private Object controllerWithAdminWebBaseUrl(String baseUrl) throws Exception {
        OrgLoginProperties properties = copyProperties(baseUrl);
        Constructor<?> constructor = OrgLoginController.class.getDeclaredConstructors()[0];
        Object[] arguments = java.util.Arrays.stream(constructor.getParameterTypes())
                .map(type -> type == OrgLoginProperties.class ? properties : applicationContext.getBean(type))
                .toArray();
        constructor.setAccessible(true);
        return constructor.newInstance(arguments);
    }

    private OrgLoginProperties copyProperties(String adminWebBaseUrl) throws Exception {
        RecordComponent[] components = OrgLoginProperties.class.getRecordComponents();
        assertThat(components).extracting(RecordComponent::getName).contains("adminWebBaseUrl");
        Class<?>[] types = java.util.Arrays.stream(components).map(RecordComponent::getType).toArray(Class<?>[]::new);
        Object[] values = new Object[components.length];
        for (int i = 0; i < components.length; i++) {
            values[i] = "adminWebBaseUrl".equals(components[i].getName())
                    ? adminWebBaseUrl
                    : components[i].getAccessor().invoke(loginProperties);
        }
        return OrgLoginProperties.class.getDeclaredConstructor(types).newInstance(values);
    }

    private static Cookie cookie(String value) {
        return new Cookie(HANDOFF_COOKIE, value);
    }

    private static String handoffPath(TenantId tenant) {
        return "/v1/auth/org/" + tenant + "/handoff";
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
