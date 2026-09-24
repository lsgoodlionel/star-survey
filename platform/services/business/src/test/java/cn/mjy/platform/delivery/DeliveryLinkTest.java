package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.security.SignedParameters;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.delivery.DeliveryLinkService.NewLink;
import cn.mjy.platform.shared.security.SignedParameters.Verdict;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/** 投放链接（R05-01、R05-07、R05-08）：签名参数、短链、二维码、内嵌代码、失效与跨租户。 */
@DeliveryIntegrationTest
class DeliveryLinkTest {

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private DeliveryLinkService links;

    @Autowired
    private DeliveryKeys keys;

    @Autowired
    private AccessFixture access;

    private Published p;

    @BeforeEach
    void publishedSurvey() {
        p = fixture.publishedSurvey();
    }

    @Test
    void theLinkPointsAtTheEnginesurveyAndCarriesTheSignedParameters() {
        LinkView link = links.create(p.owner(), p.surveyId(),
                new NewLink("微信朋友圈", Map.of("src", "wechat", "dept", "研发部"), Duration.ofDays(7), false));

        assertThat(link.url()).contains("/index.php/" + p.sid());
        assertThat(link.url()).contains("src=wechat").contains("sig=").contains("exp=");
        assertThat(link.signedParams()).containsKeys("src", "dept",
                SignedParameters.EXPIRY_PARAM, SignedParameters.SIGNATURE_PARAM);
        assertThat(SignedParameters.verify(keys.linkKey(p.tenant()),
                DeliveryLinkService.signingContext(p.surveyId()), link.signedParams(), Instant.now()))
                .isEqualTo(Verdict.VALID);
    }

    @Test
    void aTamperedParameterNoLongerVerifies() {
        LinkView link = links.create(p.owner(), p.surveyId(),
                new NewLink("渠道甲", Map.of("src", "channel-a"), Duration.ofDays(7), false));

        Map<String, String> forged = new LinkedHashMap<>(link.signedParams());
        forged.put("src", "channel-b");

        assertThat(SignedParameters.verify(keys.linkKey(p.tenant()),
                DeliveryLinkService.signingContext(p.surveyId()), forged, Instant.now()))
                .isEqualTo(Verdict.BAD_SIGNATURE);
    }

    /** 同一租户的另一份问卷不能复用签名：上下文里带问卷公开 UUID。 */
    @Test
    void parametersSignedForOneSurveyDoNotVerifyForAnother() {
        LinkView link = links.create(p.owner(), p.surveyId(),
                new NewLink("渠道甲", Map.of("src", "channel-a"), Duration.ofDays(7), false));
        UUID otherSurvey = UUID.randomUUID();

        assertThat(SignedParameters.verify(keys.linkKey(p.tenant()),
                DeliveryLinkService.signingContext(otherSurvey), link.signedParams(), Instant.now()))
                .isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void theShortLinkResolvesToTheSameRespondentUrl() {
        LinkView link = links.create(p.owner(), p.surveyId(),
                new NewLink("海报", Map.of("src", "poster"), Duration.ofDays(7), true));

        assertThat(link.shortUrl()).startsWith("https://survey.example/d/s/");
        String code = link.shortUrl().substring(link.shortUrl().lastIndexOf('/') + 1);
        assertThat(code).matches(ShortCodes.CODE_REGEX);
        assertThat(links.resolveShortLink(code)).isEqualTo(link.url());
    }

    /** 枚举不出东西：形状不对、不存在、已作废、已过期，返回的都是同一个 404。 */
    @Test
    void unknownRevokedAndExpiredShortLinksAllLookIdentical() {
        LinkView revoked = links.create(p.owner(), p.surveyId(),
                new NewLink("已作废", Map.of(), Duration.ofDays(7), true));
        links.revoke(p.owner(), revoked.id());
        LinkView expired = links.create(p.owner(), p.surveyId(),
                new NewLink("即将过期", Map.of(), Duration.ofMinutes(1), true));
        fixture.expireLink(p.tenant(), expired.id());

        for (String code : new String[] {
                "not-a-code",
                ShortCodes.random(),
                codeOf(revoked),
                codeOf(expired)}) {
            assertThatThrownBy(() -> links.resolveShortLink(code))
                    .isInstanceOf(DeliveryNotFoundException.class)
                    .hasMessage("short link not found");
        }
    }

    @Test
    void theQrCodeDecodesToTheRespondentUrl() {
        LinkView link = links.create(p.owner(), p.surveyId(),
                new NewLink("展台二维码", Map.of("src", "booth"), Duration.ofDays(7), true));

        QrCode qr = links.qrCode(p.owner(), link.id());

        assertThat(new String(QrMatrixDecoder.decode(qr), StandardCharsets.UTF_8)).isEqualTo(link.url());
    }

    @Test
    void theEmbedSnippetSandboxesTheIframeAndExplainsTheDeploymentSettings() {
        LinkView link = links.create(p.owner(), p.surveyId(), new NewLink("官网", Map.of(), null, false));

        DeliveryLinkService.Embed embed = links.embed(p.owner(), link.id(), 640, 800);

        assertThat(embed.snippet())
                .contains("sandbox=\"" + EmbedSnippet.SANDBOX + "\"")
                .contains("referrerpolicy=\"" + EmbedSnippet.REFERRER_POLICY + "\"")
                .doesNotContain("allow-top-navigation");
        assertThat(embed.notes()).anySatisfy(note -> assertThat(note).contains("frame-src"));
    }

    @Test
    void aSurveyThatHasNeverBeenPublishedHasNoLink() {
        UUID unpublished = access.survey(p.tenant(), p.ws().project());

        assertThatThrownBy(() -> links.create(p.owner(), unpublished,
                new NewLink("还没发布", Map.of(), null, false)))
                .isInstanceOf(DeliveryConflictException.class)
                .extracting(e -> ((DeliveryConflictException) e).code())
                .isEqualTo("survey_not_published");
    }

    /** 跨租户：另一个租户的所有者拿着链接 id 也只得到 404（行级安全下"不存在"与"不是你的"不可区分）。 */
    @Test
    void anotherTenantCannotReadTheLink() {
        LinkView link = links.create(p.owner(), p.surveyId(), new NewLink("自家链接", Map.of(), null, true));
        TenantContext intruder = access.newTenant();

        assertThatThrownBy(() -> links.find(intruder, link.id()))
                .isInstanceOf(DeliveryNotFoundException.class);
        assertThatThrownBy(() -> links.list(intruder, p.surveyId()))
                .isInstanceOf(DeliveryNotFoundException.class);
    }

    /** 只有查看权的成员建不了链接，但看得到；建链接要问卷范围上的编辑权。 */
    @Test
    void creatingALinkNeedsEditWhileReadingNeedsOnlyView() {
        TenantContext viewer = fixture.responses().member(p, "viewer", "statistics_viewer");
        LinkView link = links.create(p.owner(), p.surveyId(), new NewLink("给查看者看", Map.of(), null, false));

        assertThat(links.find(viewer, link.id()).id()).isEqualTo(link.id());
        assertThatThrownBy(() -> links.create(viewer, p.surveyId(), new NewLink("不该建成", Map.of(), null, false)))
                .isInstanceOf(DeliveryAccessDeniedException.class);
    }

    private String codeOf(LinkView link) {
        return link.shortUrl().substring(link.shortUrl().lastIndexOf('/') + 1);
    }
}
