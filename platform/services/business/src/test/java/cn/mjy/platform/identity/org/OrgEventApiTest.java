package cn.mjy.platform.identity.org;

import static cn.mjy.platform.identity.org.OrgEventRequests.dingtalkLeave;
import static cn.mjy.platform.identity.org.OrgEventRequests.dingtalkPost;
import static cn.mjy.platform.identity.org.OrgEventRequests.feishuChallenge;
import static cn.mjy.platform.identity.org.OrgEventRequests.feishuEvent;
import static cn.mjy.platform.identity.org.OrgEventRequests.feishuPost;
import static cn.mjy.platform.identity.org.OrgEventRequests.nowSeconds;
import static cn.mjy.platform.identity.org.OrgEventRequests.wecomContactEvent;
import static cn.mjy.platform.identity.org.OrgEventRequests.wecomPost;
import static cn.mjy.platform.identity.org.OrgEventRequests.wecomPostSealed;
import static cn.mjy.platform.identity.org.OrgEventRequests.wecomVerify;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;

/**
 * 通讯录事件回调（ADR 0014 增补二）：三家开放平台的验签、时间戳、解密、握手、去重与离职撤权。
 * 任何验签类失败都是 401 且没有副作用：成员仍在职、没有回执。
 */
class OrgEventApiTest extends OrgLoginTestSupport {

    /** 一个已免登的在职成员及其令牌。 */
    private record Member(Org org, EventSecrets secrets, String userId, String token) {
    }

    private Member member(String provider) throws Exception {
        Org org = connect(newTenant(), provider, "jit_staff");
        EventSecrets secrets = subscribeEvents(org);
        String userId = unique("u");
        FAKE.addUser(provider, org.corp(), userId);
        return new Member(org, secrets, userId, login(org, userId));
    }

    private boolean stillSignedIn(Member m) throws Exception {
        return mvc.perform(get("/v1/me").header("Authorization", "Bearer " + m.token()))
                .andReturn().getResponse().getStatus() == 200;
    }

    private long receipts(Org org) {
        return tenantScope.call(org.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM org_event_receipt WHERE connection_id = :c")
                .param("c", org.connectionId()).query(Long.class).single());
    }

    // ---- 企业微信 ----

    @Test
    void wecomUrlVerificationEchoesTheDecryptedString() throws Exception {
        Member m = member("wecom");

        mvc.perform(wecomVerify(eventPath(m.org()), m.secrets(), m.org().corp(), "1616140317555161061", nowSeconds()))
                .andExpect(status().isOk())
                .andExpect(content().string("1616140317555161061"));
        assertThat(receipts(m.org())).isZero();
    }

    @Test
    void aWecomDeleteUserEventRevokesImmediatelyAndIsDeduplicated() throws Exception {
        Member m = member("wecom");
        String message = wecomContactEvent(m.org().corp(), "delete_user", m.userId(), null);

        mvc.perform(wecomPost(eventPath(m.org()), m.secrets(), m.org().corp(), message, nowSeconds()))
                .andExpect(status().isOk());
        mvc.perform(wecomPost(eventPath(m.org()), m.secrets(), m.org().corp(), message, nowSeconds()))
                .andExpect(status().isOk());

        assertThat(stillSignedIn(m)).isFalse();
        assertThat(receipts(m.org())).isEqualTo(1);
    }

    @Test
    void aWecomUpdateUserEventRevokesOnlyForDisabledOrLeftStatus() throws Exception {
        Member active = member("wecom");
        Member disabled = member("wecom");

        mvc.perform(wecomPost(eventPath(active.org()), active.secrets(), active.org().corp(),
                wecomContactEvent(active.org().corp(), "update_user", active.userId(), "1"), nowSeconds()))
                .andExpect(status().isOk());
        mvc.perform(wecomPost(eventPath(disabled.org()), disabled.secrets(), disabled.org().corp(),
                wecomContactEvent(disabled.org().corp(), "update_user", disabled.userId(), "2"), nowSeconds()))
                .andExpect(status().isOk());

        assertThat(stillSignedIn(active)).isTrue();
        assertThat(stillSignedIn(disabled)).isFalse();
    }

    @Test
    void aForgedWecomEventIsRejectedWithoutSideEffects() throws Exception {
        Member m = member("wecom");
        String message = wecomContactEvent(m.org().corp(), "delete_user", m.userId(), null);
        EventSecrets wrongToken = new EventSecrets("not-the-token", m.secrets().key());
        String sealed = OrgEventCrypto.sealWxBiz(m.secrets().key(), message, m.org().corp());

        mvc.perform(wecomPost(eventPath(m.org()), wrongToken, m.org().corp(), message, nowSeconds()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value(OrgEventException.INVALID_SIGNATURE));
        // 签名对，但密文是给别的企业的（receiveid 不符）。
        mvc.perform(wecomPost(eventPath(m.org()), m.secrets(), "other-corp", message, nowSeconds()))
                .andExpect(status().isUnauthorized());
        // 过期的时间戳（重放）。
        mvc.perform(wecomPost(eventPath(m.org()), m.secrets(), m.org().corp(), message, nowSeconds() - 3600))
                .andExpect(status().isUnauthorized());
        // 没有签名参数。
        mvc.perform(post(eventPath(m.org())).contentType(MediaType.TEXT_XML)
                        .content("<xml><Encrypt>" + sealed + "</Encrypt></xml>"))
                .andExpect(status().isUnauthorized());
        // 签名覆盖的是另一段密文。
        mvc.perform(wecomPostSealed(eventPath(m.org()), m.secrets().token(), sealed + "x",
                        Long.toString(nowSeconds()), m.org().corp()))
                .andExpect(status().isUnauthorized());

        assertThat(stillSignedIn(m)).isTrue();
        assertThat(receipts(m.org())).isZero();
    }

    @Test
    void wecomBodiesWithADoctypeAreRejected() throws Exception {
        Member m = member("wecom");
        String xxe = "<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY e SYSTEM \"file:///etc/passwd\">]>"
                + "<xml><Encrypt>&e;</Encrypt></xml>";

        mvc.perform(post(eventPath(m.org()) + "?msg_signature=a&timestamp=1&nonce=n")
                        .contentType(MediaType.TEXT_XML).content(xxe))
                .andExpect(status().isUnauthorized());
    }

    // ---- 钉钉 ----

    @Test
    void dingtalkCheckUrlIsAnsweredWithAnEncryptedSignedSuccess() throws Exception {
        Member m = member("dingtalk");

        String body = mvc.perform(dingtalkPost(eventPath(m.org()), m.secrets(), m.org().appId(),
                        "{\"EventType\":\"check_url\"}", System.currentTimeMillis()))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();

        String encrypt = JsonPath.read(body, "$.encrypt");
        String expected = OrgEventCrypto.sortedSha1(m.secrets().token(), JsonPath.read(body, "$.timeStamp"),
                JsonPath.read(body, "$.nonce"), encrypt);
        assertThat((String) JsonPath.read(body, "$.msg_signature")).isEqualTo(expected);
        assertThat(OrgEventCrypto.openWxBiz(m.secrets().key(), encrypt))
                .isEqualTo(new OrgEventCrypto.Envelope("success", m.org().appId()));
        assertThat(receipts(m.org())).isZero();
    }

    @Test
    void aDingtalkUserLeaveOrgEventRevokesEveryListedMember() throws Exception {
        Member m = member("dingtalk");
        String other = unique("u");
        FAKE.addUser("dingtalk", m.org().corp(), other);
        String otherToken = login(m.org(), other);

        mvc.perform(dingtalkPost(eventPath(m.org()), m.secrets(), m.org().appId(),
                        dingtalkLeave(m.org().corp(), m.userId(), other), System.currentTimeMillis()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.encrypt").isNotEmpty());

        assertThat(stillSignedIn(m)).isFalse();
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + otherToken))
                .andExpect(status().isUnauthorized());
        assertThat(receipts(m.org())).isEqualTo(1);
    }

    @Test
    void aForgedDingtalkEventIsRejectedWithoutSideEffects() throws Exception {
        Member m = member("dingtalk");
        String leave = dingtalkLeave(m.org().corp(), m.userId());

        mvc.perform(dingtalkPost(eventPath(m.org()), new EventSecrets("wrong", m.secrets().key()), m.org().appId(),
                        leave, System.currentTimeMillis()))
                .andExpect(status().isUnauthorized());
        mvc.perform(dingtalkPost(eventPath(m.org()), m.secrets(), "another-app-key", leave,
                        System.currentTimeMillis()))
                .andExpect(status().isUnauthorized());
        mvc.perform(dingtalkPost(eventPath(m.org()), m.secrets(), m.org().appId(),
                        dingtalkLeave("another-corp", m.userId()), System.currentTimeMillis()))
                .andExpect(status().isUnauthorized());
        mvc.perform(dingtalkPost(eventPath(m.org()), m.secrets(), m.org().appId(), leave,
                        System.currentTimeMillis() - 3_600_000))
                .andExpect(status().isUnauthorized());

        assertThat(stillSignedIn(m)).isTrue();
        assertThat(receipts(m.org())).isZero();
    }

    // ---- 飞书 ----

    @Test
    void feishuUrlVerificationReturnsTheChallengeWhenTheTokenMatches() throws Exception {
        Member m = member("feishu");

        mvc.perform(feishuPost(eventPath(m.org()), m.secrets().key(), feishuChallenge(m.secrets().token(), "ch-1"),
                        true, nowSeconds()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.challenge").value("ch-1"));
        mvc.perform(feishuPost(eventPath(m.org()), m.secrets().key(), feishuChallenge("wrong", "ch-2"), true,
                        nowSeconds()))
                .andExpect(status().isUnauthorized());
        mvc.perform(feishuPost(eventPath(m.org()), "another-key", feishuChallenge(m.secrets().token(), "ch-3"), true,
                        nowSeconds()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void anUnsignedFeishuRequestIsRejectedBeforeAnyDecryption() throws Exception {
        // 未签名即解密会把端点变成 CBC 填充预言机：匿名方可逐字节解密并伪造事件。
        Member m = member("feishu");

        mvc.perform(feishuPost(eventPath(m.org()), m.secrets().key(), feishuChallenge(m.secrets().token(), "ch-u"),
                        false, nowSeconds()))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void everyRejectionLooksTheSameToAnAnonymousCaller() throws Exception {
        Member feishu = member("feishu");
        Member wecom = member("wecom");
        // 32 字节任意"密文"（前 16 字节当作 IV）：填充几乎必然非法。
        String garbage = "{\"encrypt\":\"" + java.util.Base64.getEncoder().encodeToString(new byte[32]) + "\"}";
        String doctype = "<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY e \"x\">]><xml><Encrypt>&e;</Encrypt></xml>";

        List<String> bodies = List.of(
                mvc.perform(post(eventPath(feishu.org())).contentType(MediaType.APPLICATION_JSON).content(garbage))
                        .andExpect(status().isUnauthorized()).andReturn().getResponse().getContentAsString(),
                mvc.perform(post(eventPath(feishu.org())).contentType(MediaType.APPLICATION_JSON).content("{}"))
                        .andExpect(status().isUnauthorized()).andReturn().getResponse().getContentAsString(),
                mvc.perform(feishuPost(eventPath(feishu.org()), "another-key",
                                feishuChallenge(feishu.secrets().token(), "c"), true, nowSeconds()))
                        .andExpect(status().isUnauthorized()).andReturn().getResponse().getContentAsString(),
                mvc.perform(post(eventPath(wecom.org()) + "?msg_signature=a&timestamp=1&nonce=n")
                                .contentType(MediaType.TEXT_XML).content(doctype))
                        .andExpect(status().isUnauthorized()).andReturn().getResponse().getContentAsString());

        assertThat(bodies).allMatch(body -> body.equals(bodies.getFirst()));
        assertThat(bodies.getFirst()).doesNotContain("decrypt", "padding", "signature is", "token", "DOCTYPE");
    }

    @Test
    void aFeishuDeletedEventRevokesAndTheSameEventIdIsProcessedOnce() throws Exception {
        Member m = member("feishu");
        String eventId = UUID.randomUUID().toString();
        String event = feishuEvent(eventId, "contact.user.deleted_v3", m.secrets().token(), m.org().appId(),
                m.org().corp(), m.userId(), false);

        mvc.perform(feishuPost(eventPath(m.org()), m.secrets().key(), event, true, nowSeconds()))
                .andExpect(status().isOk());
        mvc.perform(feishuPost(eventPath(m.org()), m.secrets().key(), event, true, nowSeconds()))
                .andExpect(status().isOk());

        assertThat(stillSignedIn(m)).isFalse();
        assertThat(receipts(m.org())).isEqualTo(1);
    }

    @Test
    void aFeishuUpdatedEventRevokesOnlyWhenResignedOrFrozen() throws Exception {
        Member staying = member("feishu");
        Member resigned = member("feishu");

        mvc.perform(feishuPost(eventPath(staying.org()), staying.secrets().key(),
                        feishuEvent(UUID.randomUUID().toString(), "contact.user.updated_v3", staying.secrets().token(),
                                staying.org().appId(), staying.org().corp(), staying.userId(), false),
                        true, nowSeconds()))
                .andExpect(status().isOk());
        mvc.perform(feishuPost(eventPath(resigned.org()), resigned.secrets().key(),
                        feishuEvent(UUID.randomUUID().toString(), "contact.user.updated_v3", resigned.secrets().token(),
                                resigned.org().appId(), resigned.org().corp(), resigned.userId(), true),
                        true, nowSeconds()))
                .andExpect(status().isOk());

        assertThat(stillSignedIn(staying)).isTrue();
        assertThat(stillSignedIn(resigned)).isFalse();
    }

    @Test
    void forgedOrUnsignedFeishuEventsAreRejectedWithoutSideEffects() throws Exception {
        Member m = member("feishu");
        String path = eventPath(m.org());
        String key = m.secrets().key();
        String token = m.secrets().token();
        String deleted = "contact.user.deleted_v3";

        // 事件必须带签名头。
        mvc.perform(feishuPost(path, key, feishuEvent("e1", deleted, token, m.org().appId(), m.org().corp(),
                m.userId(), false), false, nowSeconds())).andExpect(status().isUnauthorized());
        // 令牌不符、应用不符、组织不符。
        mvc.perform(feishuPost(path, key, feishuEvent("e2", deleted, "wrong", m.org().appId(), m.org().corp(),
                m.userId(), false), true, nowSeconds())).andExpect(status().isUnauthorized());
        mvc.perform(feishuPost(path, key, feishuEvent("e3", deleted, token, "cli_other", m.org().corp(),
                m.userId(), false), true, nowSeconds())).andExpect(status().isUnauthorized());
        mvc.perform(feishuPost(path, key, feishuEvent("e4", deleted, token, m.org().appId(), "other-tenant",
                m.userId(), false), true, nowSeconds())).andExpect(status().isUnauthorized());
        // 过期的签名时间戳。
        mvc.perform(feishuPost(path, key, feishuEvent("e5", deleted, token, m.org().appId(), m.org().corp(),
                m.userId(), false), true, nowSeconds() - 3600)).andExpect(status().isUnauthorized());
        // 签名不对（时间戳与随机串齐全）。
        mvc.perform(feishuPost(path, key, feishuEvent("e6", deleted, token, m.org().appId(), m.org().corp(),
                        m.userId(), false), false, nowSeconds())
                        .header(FeishuEventReceiver.TIMESTAMP_HEADER, Long.toString(nowSeconds()))
                        .header(FeishuEventReceiver.NONCE_HEADER, "n1")
                        .header(FeishuEventReceiver.SIGNATURE_HEADER, "0".repeat(64)))
                .andExpect(status().isUnauthorized());
        // 明文体（未加密）不接受。
        mvc.perform(post(path).contentType(MediaType.APPLICATION_JSON).content(feishuEvent("e7", deleted, token,
                m.org().appId(), m.org().corp(), m.userId(), false))).andExpect(status().isUnauthorized());

        assertThat(stillSignedIn(m)).isTrue();
        assertThat(receipts(m.org())).isZero();
    }

    // ---- 通用 ----

    @Test
    void oversizedBodiesAreRejectedBeforeParsing() throws Exception {
        Member m = member("feishu");

        mvc.perform(post(eventPath(m.org())).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"encrypt\":\"" + "A".repeat(70_000) + "\"}"))
                .andExpect(status().isPayloadTooLarge());
    }

    @Test
    void unknownConnectionsAndConnectionsWithoutASubscriptionAreNotFound() throws Exception {
        Org plain = connect(newTenant(), "wecom", null);

        mvc.perform(post(eventPath(plain)).contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/v1/org-events/" + plain.tenant() + "/" + UUID.randomUUID()).content("{}"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/v1/org-events/not-a-tenant/not-a-connection").content("{}"))
                .andExpect(status().isNotFound());
    }

    /** 一个租户的连接地址配上另一个租户：行级安全下找不到连接。 */
    @Test
    void anEventAddressedThroughAnotherTenantIsNotFound() throws Exception {
        Member m = member("wecom");
        TenantId other = newTenant();
        String message = wecomContactEvent(m.org().corp(), "delete_user", m.userId(), null);

        mvc.perform(wecomPost("/v1/org-events/" + other + "/" + m.org().connectionId(), m.secrets(),
                        m.org().corp(), message, nowSeconds()))
                .andExpect(status().isNotFound());
        assertThat(stillSignedIn(m)).isTrue();
    }

    @Test
    void theConnectionViewShowsTheEventUrlAndOnlySecretNames() throws Exception {
        Org org = connect(newTenant(), "feishu", null);
        EventSecrets secrets = subscribeEvents(org);

        String body = mvc.perform(get("/v1/org-connections/" + org.connectionId())
                        .header("Authorization", ownerBearer(org.tenant())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.eventUrl").value(CALLBACK_BASE + eventPath(org)))
                .andReturn().getResponse().getContentAsString();

        assertThat(body).doesNotContain(secrets.token()).doesNotContain(secrets.key());
    }

    @Test
    void eventSecretReferencesMustBeGivenTogether() throws Exception {
        Org org = connect(newTenant(), "wecom", null);

        mvc.perform(put("/v1/org-connections/" + org.connectionId())
                        .header("Authorization", ownerBearer(org.tenant()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"eventTokenRef\":\"ONLY_TOKEN\"}"))
                .andExpect(status().isBadRequest());
    }
}
