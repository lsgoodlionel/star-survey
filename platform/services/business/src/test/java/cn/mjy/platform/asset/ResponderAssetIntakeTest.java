package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * 作答者上传的入库（ADR 0019 决定 8）：资产行由<b>上传会话</b>驱动，
 * {@code upload_token} 即资产 id，平台侧重新过三道闸门。
 *
 * <p>这里钉住的不变式：
 * <ol>
 *   <li>入库只 {@code INSERT}，重复拉取幂等，<b>伪造的 upload_token 顶不掉已有资产</b>；</li>
 *   <li>平台的上传闸门一条都不许退（类型、魔数、SVG、空文件、大小）；</li>
 *   <li>作答者资产与作者素材<b>不是同一种东西</b>：不进素材库列表、不许追加版本、不许从库里删。</li>
 * </ol>
 */
@AssetIntegrationTest
class ResponderAssetIntakeTest {

    private static final String INSTANCE = "engine-a";
    private static final long SID = 4242L;
    private static final String GENERATION = "gen-1";
    private static final long RESPONSE = 17L;
    private static final String CODE = "Q1";
    private static final String ALICE = "invite-code-alice";

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private ResponderAssetService responderAssets;

    @Autowired
    private AssetService assets;

    private TenantContext owner;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
    }

    @Test
    void anUploadSessionBecomesAnAssetWhoseIdIsTheUploadToken() {
        UUID token = UUID.randomUUID();

        AssetView view = responderAssets.ingest(owner, upload(token, "audio/webm", AssetFixture.webm()));

        assertThat(view.id()).isEqualTo(token);
        assertThat(view.kind()).isEqualTo(AssetKind.AUDIO);
        assertThat(view.currentVersion()).isEqualTo(1);
        assertThat(responderAssets.originOf(owner, token)).isEqualTo(AssetOrigin.RESPONDENT);
    }

    /** 同一次上传拉两遍只产生一件资产：幂等由主键给，不由"先查一下有没有"给。 */
    @Test
    void ingestingTheSameUploadTwiceYieldsOneAsset() {
        UUID token = UUID.randomUUID();
        ResponderUpload upload = upload(token, "audio/webm", AssetFixture.webm());

        AssetView first = responderAssets.ingest(owner, upload);
        AssetView again = responderAssets.ingest(owner, upload);

        assertThat(again.id()).isEqualTo(first.id());
        assertThat(again.currentVersion()).isEqualTo(1);
        assertThat(responderAssets.forResponse(owner, INSTANCE, SID, GENERATION, RESPONSE)).hasSize(1);
    }

    /**
     * 篡改用例：伪造一个 {@code upload_token}，让它撞上一件作者素材的 id。
     * 入库绝不能把作者的字节换掉——那是一条从"引擎侧随便编一个 UUID"到"改写平台素材"的直通路。
     */
    @Test
    void aForgedUploadTokenCannotOverwriteAnAuthorAsset() {
        AssetView authorAsset = assets.create(owner, "封面", new AssetUpload("image/png", "c.png", AssetFixture.png()));

        assertThatThrownBy(() -> responderAssets.ingest(owner, upload(authorAsset.id(), "image/gif",
                AssetFixture.gif())))
                .isInstanceOf(AssetConflictException.class)
                .extracting(e -> ((AssetConflictException) e).code())
                .isEqualTo(AssetConflictException.ASSET_ID_TAKEN);

        AssetView unchanged = assets.get(owner, authorAsset.id());
        assertThat(unchanged.sha256()).isEqualTo(authorAsset.sha256());
        assertThat(unchanged.currentVersion()).isEqualTo(1);
    }

    /** 同一个 token 第二次带着<b>别的</b>字节回来：不是重放，是篡改。 */
    @Test
    void thesameUploadTokenWithDifferentBytesIsRefused() {
        UUID token = UUID.randomUUID();
        responderAssets.ingest(owner, upload(token, "audio/webm", AssetFixture.webm()));

        assertThatThrownBy(() -> responderAssets.ingest(owner, upload(token, "image/png", AssetFixture.png())))
                .isInstanceOf(AssetConflictException.class);
    }

    /** 引擎侧的上传限制不是平台的闸门：声明与内容不符照样拒收。 */
    @Test
    void aresponderUploadStillFacesTheMagicByteGate() {
        assertThatThrownBy(() -> responderAssets.ingest(owner,
                upload(UUID.randomUUID(), "image/png", AssetFixture.gif())))
                .isInstanceOf(InvalidAssetException.class);
    }

    @Test
    void aresponderUploadCannotSmuggleAnSvg() {
        assertThatThrownBy(() -> responderAssets.ingest(owner,
                upload(UUID.randomUUID(), "image/svg+xml", AssetFixture.svg())))
                .isInstanceOf(InvalidAssetException.class);
    }

    @Test
    void anEmptyResponderUploadIsRefused() {
        assertThatThrownBy(() -> responderAssets.ingest(owner,
                upload(UUID.randomUUID(), "image/png", new byte[0])))
                .isInstanceOf(InvalidAssetException.class);
    }

    /** 作答者资产不是素材：它不该出现在作者的素材库列表里。 */
    @Test
    void responderAssetsDoNotShowUpInTheAuthorLibrary() {
        AssetView authorAsset = assets.create(owner, "封面", new AssetUpload("image/png", "c.png", AssetFixture.png()));
        AssetView recording = responderAssets.ingest(owner,
                upload(UUID.randomUUID(), "audio/webm", AssetFixture.webm()));

        List<UUID> listed = assets.list(owner, 50).stream().map(AssetView::id).toList();

        assertThat(listed).contains(authorAsset.id()).doesNotContain(recording.id());
    }

    /** 一次作答上传就是一版：版本留存在这里没有意义，追加新版本等于改写答卷数据。 */
    @Test
    void anAuthorCannotAddAversionToAresponderAsset() {
        AssetView recording = responderAssets.ingest(owner,
                upload(UUID.randomUUID(), "audio/webm", AssetFixture.webm()));

        assertThatThrownBy(() -> assets.addVersion(owner, recording.id(),
                new AssetUpload("audio/webm", "x.webm", AssetFixture.webm())))
                .isInstanceOf(AssetConflictException.class)
                .extracting(e -> ((AssetConflictException) e).code())
                .isEqualTo(AssetConflictException.RESPONDENT_ASSET);
    }

    /** 它是答卷数据，随答卷的保留策略走，不从素材库上点一下就删掉。 */
    @Test
    void anAuthorCannotDeleteAresponderAssetFromTheLibrary() {
        AssetView recording = responderAssets.ingest(owner,
                upload(UUID.randomUUID(), "audio/webm", AssetFixture.webm()));

        assertThatThrownBy(() -> assets.delete(owner, recording.id()))
                .isInstanceOf(AssetConflictException.class)
                .extracting(e -> ((AssetConflictException) e).code())
                .isEqualTo(AssetConflictException.RESPONDENT_ASSET);
    }

    /** 来源证据按答卷可查：作者审阅答卷时靠它找到那段录音。 */
    @Test
    void theIntakeRecordsWhichAnswerTheUploadBelongsTo() {
        UUID token = UUID.randomUUID();
        responderAssets.ingest(owner, upload(token, "audio/webm", AssetFixture.webm()));

        List<ResponderAssetView> found = responderAssets.forResponse(owner, INSTANCE, SID, GENERATION, RESPONSE);

        assertThat(found).singleElement().satisfies(item -> {
            assertThat(item.assetId()).isEqualTo(token);
            assertThat(item.questionCode()).isEqualTo(CODE);
            assertThat(item.hasRespondentBinding()).isTrue();
        });
        assertThat(responderAssets.forResponse(owner, INSTANCE, SID, GENERATION, RESPONSE + 1)).isEmpty();
    }

    /** 令牌不落平台库：库里只有不可逆的指纹。 */
    @Test
    void theParticipantTokenItselfIsNeverStored() {
        UUID token = UUID.randomUUID();
        responderAssets.ingest(owner, upload(token, "audio/webm", AssetFixture.webm()));

        assertThat(responderAssets.respondentKeyOf(owner, token)).isPresent().get(
                org.assertj.core.api.InstanceOfAssertFactories.STRING)
                .matches("[0-9a-f]{64}")
                .isNotEqualTo(ALICE);
    }

    /** 匿名作答没有令牌：资产照样入库，但一张绑定票都签不出来——失败即关闭。 */
    @Test
    void anAnonymousUploadIsIngestedButCannotBeTicketed() {
        UUID token = UUID.randomUUID();
        ResponderUpload anonymous = new ResponderUpload(token, INSTANCE, SID, GENERATION, RESPONSE, CODE,
                null, "audio/webm", "a.webm", AssetFixture.webm());

        AssetView view = responderAssets.ingest(owner, anonymous);

        assertThat(view.id()).isEqualTo(token);
        assertThat(responderAssets.respondentKeyOf(owner, token)).isEmpty();
        assertThatThrownBy(() -> responderAssets.mintTicket(owner, token, 1))
                .isInstanceOf(AssetConflictException.class)
                .extracting(e -> ((AssetConflictException) e).code())
                .isEqualTo(AssetConflictException.NO_RESPONDENT_BINDING);
    }

    /** 跨租户：别的租户拉过的 token 在这里等同不存在。 */
    @Test
    void anotherTenantsResponderAssetIsInvisibleHere() {
        TenantContext stranger = fixture.newTenant();
        UUID token = UUID.randomUUID();
        responderAssets.ingest(stranger, upload(token, "audio/webm", AssetFixture.webm()));

        assertThat(responderAssets.forResponse(owner, INSTANCE, SID, GENERATION, RESPONSE)).isEmpty();
        assertThatThrownBy(() -> responderAssets.mintTicket(owner, token, 1))
                .isInstanceOf(AssetNotFoundException.class);
    }

    /** 只有 view 没有 edit 的人拉不动入库——它是写操作。 */
    @Test
    void aviewerCannotIngest() {
        TenantContext viewer = fixture.viewer(owner, "someone");

        assertThatThrownBy(() -> responderAssets.ingest(viewer,
                upload(UUID.randomUUID(), "audio/webm", AssetFixture.webm())))
                .isInstanceOf(AssetAccessDeniedException.class);
    }

    private static ResponderUpload upload(UUID token, String contentType, byte[] content) {
        return new ResponderUpload(token, INSTANCE, SID, GENERATION, RESPONSE, CODE, ALICE,
                contentType, "answer" + token + ".bin", content);
    }
}
