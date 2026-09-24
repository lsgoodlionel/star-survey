package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.FakePublishGateway;
import cn.mjy.platform.survey.PublishUnavailableException;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyPublishService;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyView;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 没有配资产主密钥（{@code PLATFORM_ASSET_SECRET}）时的行为：带资产引用的问卷<b>发不出去</b>。
 *
 * <p>这个上下文<b>故意不带</b>主密钥（与 {@link AssetIntegrationTest} 相反），所以它同时钉住两件事：
 * 失败即关闭（绝不发一份图全裂的问卷），以及失败要落在"服务当下不可用"这个语义上——
 * 对外 503 而不是 500。资产模块自己的错误映射只覆盖 {@code /v1/assets}，
 * 发布走的是问卷模块的控制器，所以接缝必须抛一个问卷模块认识的异常。
 */
@SpringBootTest
class SurveyAssetUnavailableTest {

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
    void aSurveyThatReferencesAnAssetCannotBePublishedWithoutTheAssetSecret() {
        AssetView asset = assets.create(owner, "封面", new AssetUpload("image/png", "c.png", AssetFixture.png()));
        SurveyView survey = surveys.create(owner, ws.project(), definitionWithSlide(asset.id().toString()));

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(PublishUnavailableException.class);
        assertThat(gateway.callsFor(survey.id())).isEmpty();
    }

    /** 没有资产引用的问卷照常发布：缺主密钥只挡住真正用到资产的那些。 */
    @Test
    void aSurveyWithoutAssetsIsUnaffected() {
        SurveyView survey = fixture.newSurvey(ws);

        publisher.publish(owner, survey.id());

        assertThat(gateway.callsFor(survey.id())).hasSize(1);
    }

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
}
