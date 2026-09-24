package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.FakePublishGateway;
import cn.mjy.platform.survey.InvalidDefinitionException;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyPublishService;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyView;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 发布时把定义里的资产引用解析成签名地址，并把这一版钉死（ADR 0019 决定 5）。
 *
 * <p>钉住的是三件事：作者只写 {@code assetId}、网关只看见 {@code url}；解析取的是发布那一刻的版本，
 * 之后再上传新版本也改不动已发布的快照；引用登记之后资产删不掉。
 */
@AssetIntegrationTest
class SurveyAssetPublishTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private AssetService assets;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private JsonMapper json;

    private Workspace ws;
    private TenantContext owner;

    @BeforeEach
    void seed() {
        ws = fixture.workspace();
        owner = ws.owner();
    }

    @Test
    void publishingRewritesAssetReferencesIntoSignedAddresses() {
        AssetView asset = image("封面");
        SurveyView survey = surveyWithSlides(asset.id());

        publisher.publish(owner, survey.id());

        JsonNode slide = firstSlideSentToGateway(survey.id());
        assertThat(slide.has("assetId")).as("assetId 不该发给网关").isFalse();
        assertThat(slide.get("assetVersion").intValue()).isEqualTo(1);
        assertThat(slide.get("url").asString())
                .startsWith("https://survey.example/a/" + owner.tenantId().value() + "/" + asset.id() + "/1?")
                .contains("exp=").contains("sig=");
        // 替代文本原样保留：无障碍是需求验收的一条。
        assertThat(slide.get("alt").asString()).isEqualTo("红色包装");
    }

    /** 媒体版本留存：发布之后再上传新版本，已发出去的那一份纹丝不动。 */
    @Test
    void aNewerVersionDoesNotLeakIntoAnAlreadyPublishedSnapshot() {
        AssetView asset = image("封面");
        SurveyView survey = surveyWithSlides(asset.id());
        publisher.publish(owner, survey.id());
        String publishedUrl = firstSlideSentToGateway(survey.id()).get("url").asString();

        assets.addVersion(owner, asset.id(), new AssetUpload("image/jpeg", "new.jpg", AssetFixture.jpeg()));

        assertThat(firstSlideSentToGateway(survey.id()).get("url").asString()).isEqualTo(publishedUrl);
        assertThat(publishedUrl).contains("/" + asset.id() + "/1?");
    }

    @Test
    void aPublishedAssetCanNoLongerBeDeletedButCanStillBeArchived() {
        AssetView asset = image("封面");
        SurveyView survey = surveyWithSlides(asset.id());
        publisher.publish(owner, survey.id());

        assertThatThrownBy(() -> assets.delete(owner, asset.id()))
                .isInstanceOf(AssetConflictException.class)
                .hasMessageContaining("referenced");
        assets.archive(owner, asset.id());
        assertThat(assets.get(owner, asset.id()).status()).isEqualTo(AssetStatus.ARCHIVED);
    }

    @Test
    void publishingAnUnknownAssetFails() {
        SurveyView survey = surveyWithSlides(UUID.randomUUID());

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
        assertThat(gateway.callsFor(survey.id())).isEmpty();
    }

    /** 别的租户的资产 id：在行级安全下等同不存在，发布失败，绝不悄悄发一份图全裂的问卷。 */
    @Test
    void publishingAnotherTenantsAssetFails() {
        Workspace other = fixture.workspace();
        AssetView theirs = assets.create(other.owner(), "别家的",
                new AssetUpload("image/png", "x.png", AssetFixture.png()));
        SurveyView survey = surveyWithSlides(theirs.id());

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
        assertThat(gateway.callsFor(survey.id())).isEmpty();
    }

    @Test
    void publishingAnArchivedAssetFails() {
        AssetView asset = image("封面");
        assets.archive(owner, asset.id());
        SurveyView survey = surveyWithSlides(asset.id());

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
    }

    /** 作者不能自带地址：写了 url 的引用直接判定义非法，否则资产服务就白做了。 */
    @Test
    void anAuthorSuppliedUrlNextToAnAssetIdIsRefused() {
        AssetView asset = image("封面");
        ObjectNode definition = definitionWithSlide(asset.id().toString());
        ((ObjectNode) definition.get("groups").get(0).get("questions").get(0)
                .get("themeOptions").get("slides").get(0)).put("url", "https://evil.example/x.png");
        SurveyView survey = surveys.create(owner, ws.project(), definition);

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
    }

    /**
     * 最要紧的一条：作者**根本不写 assetId**，直接把 url ＋ assetVersion 填成一张幻灯片。
     *
     * <p>独立安全审查抓到的：改写器只看带 assetId 的对象，这种对象它从来没访问过，于是
     * 一串任意地址会原样发到作答页上——版本留存、上传闸门、同源取件全部绕开，
     * 作答者的浏览器被指使去访问第三方。资产服务就白做了。
     */
    @Test
    void anAuthorSuppliedAddressWithoutAnAssetIdIsRefused() {
        ObjectNode definition = fixture.definition();
        ObjectNode question = (ObjectNode) definition.get("groups").get(0).get("questions").get(0);
        question.put("theme", "mjy-carousel");
        ObjectNode options = json.createObjectNode();
        ObjectNode slide = options.putArray("slides").addObject();
        slide.put("code", "s1");
        slide.put("url", "https://evil.example/tracker.png");
        slide.put("alt", "看起来很正常");
        slide.put("assetVersion", 1);
        question.set("themeOptions", options);
        SurveyView survey = surveys.create(owner, ws.project(), definition);

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
        assertThat(gateway.callsFor(survey.id())).isEmpty();
    }

    /** 版本号也是平台写的：作者自己钉一个旧版本同样不许。 */
    @Test
    void anAuthorSuppliedAssetVersionIsRefused() {
        AssetView asset = image("封面");
        ObjectNode definition = definitionWithSlide(asset.id().toString());
        ((ObjectNode) definition.get("groups").get(0).get("questions").get(0)
                .get("themeOptions").get("slides").get(0)).put("assetVersion", 1);
        SurveyView survey = surveys.create(owner, ws.project(), definition);

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
        assertThat(gateway.callsFor(survey.id())).isEmpty();
    }

    private AssetView image(String name) {
        return assets.create(owner, name, new AssetUpload("image/png", "cover.png", AssetFixture.png()));
    }

    private SurveyView surveyWithSlides(UUID assetId) {
        return surveys.create(owner, ws.project(), definitionWithSlide(assetId.toString()));
    }

    /** 样例定义的第一道题换成轮播图：一张幻灯片，引用一件资产。 */
    private ObjectNode definitionWithSlide(String assetId) {
        ObjectNode definition = fixture.definition();
        ObjectNode question = (ObjectNode) definition.get("groups").get(0).get("questions").get(0);
        question.put("theme", "mjy-carousel");
        ObjectNode options = json.createObjectNode();
        ObjectNode slide = options.putArray("slides").addObject();
        slide.put("code", "s1");
        slide.put("assetId", assetId);
        slide.put("alt", "红色包装");
        question.set("themeOptions", options);
        return definition;
    }

    private JsonNode firstSlideSentToGateway(UUID surveyId) {
        List<GatewayRequest> calls = gateway.callsFor(surveyId);
        JsonNode definition = json.readTree(calls.get(calls.size() - 1).definitionJson());
        return definition.get("groups").get(0).get("questions").get(0)
                .get("themeOptions").get("slides").get(0);
    }
}
