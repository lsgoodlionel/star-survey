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
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 「作答者上传的资产不能被问卷定义引用」（ADR 0019 决定 8）。
 *
 * <p>这不是洁癖，是一条直通路：若作者能把 {@code assetId} 填成某位作答者的录音，
 * 发布时它会被改写成一张 <b>bearer</b> 取件票发到所有作答者的浏览器上——
 * 决定 9 那一整套按身份授权当场被绕开。所以解析引用时硬性要求 {@code origin = 'author'}，
 * 否则整个发布失败，与"资产不存在"同一个出口。
 */
@AssetIntegrationTest
class SurveyResponderAssetGuardTest {

    private static final String INSTANCE = "engine-a";
    private static final long SID = 4242L;
    private static final String GENERATION = "gen-1";

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private ResponderAssetService responderAssets;

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
    void aresponderUploadCannotBePublishedAsSurveyMedia() {
        UUID recording = UUID.randomUUID();
        responderAssets.ingest(owner, new ResponderUpload(recording, INSTANCE, SID, GENERATION, 11L, "Q1",
                "invite-code-alice", "audio/webm", "a.webm", AssetFixture.webm()));
        SurveyView survey = surveys.create(owner, ws.project(), definitionWithSlide(recording.toString()));

        assertThatThrownBy(() -> publisher.publish(owner, survey.id()))
                .isInstanceOf(InvalidDefinitionException.class);
        assertThat(gateway.callsFor(survey.id())).isEmpty();
    }

    private ObjectNode definitionWithSlide(String assetId) {
        ObjectNode definition = fixture.definition();
        ObjectNode question = (ObjectNode) definition.get("groups").get(0).get("questions").get(0);
        question.put("theme", "mjy-carousel");
        ObjectNode options = json.createObjectNode();
        ObjectNode slide = options.putArray("slides").addObject();
        slide.put("code", "s1");
        slide.put("assetId", assetId);
        slide.put("alt", "看起来很正常");
        question.set("themeOptions", options);
        return definition;
    }
}
