package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 上传是系统边界（ADR 0019 决定 3）：类型白名单、大小上限、内容魔数三道闸门，
 * 以及"存下来的内容类型以嗅探为准，不以调用方声明为准"。
 */
@SpringBootTest
class AssetUploadGateTest {

    @Autowired
    private AssetFixture fixture;

    @Autowired
    private AssetService assets;

    @Autowired
    private AssetProperties properties;

    private TenantContext owner;

    @BeforeEach
    void seed() {
        owner = fixture.newTenant();
    }

    @Test
    void anEmptyFileIsRejected() {
        assertThatThrownBy(() -> assets.create(owner, "空", new AssetUpload("image/png", "e.png", new byte[0])))
                .isInstanceOf(InvalidAssetException.class)
                .hasMessageContaining("empty");
    }

    @Test
    void aFileLargerThanTheLimitIsRejected() {
        byte[] tooBig = new byte[(int) properties.maxBytes() + 1];
        System.arraycopy(AssetFixture.png(), 0, tooBig, 0, 8);

        assertThatThrownBy(() -> assets.create(owner, "大", new AssetUpload("image/png", "big.png", tooBig)))
                .isInstanceOf(InvalidAssetException.class)
                .hasMessageContaining("too large");
    }

    @Test
    void aContentTypeOutsideTheAllowListIsRejected() {
        assertThatThrownBy(() -> assets.create(owner, "文本",
                new AssetUpload("text/html", "x.html", "<h1>hi</h1>".getBytes(StandardCharsets.UTF_8))))
                .isInstanceOf(InvalidAssetException.class)
                .hasMessageContaining("unsupported content type");
    }

    /** SVG 是可执行文档：在平台域名下回放等于给了任意脚本执行点，所以整类拒绝，不看内容。 */
    @Test
    void svgIsRejectedEvenWhenItIsHonestlyDeclared() {
        assertThatThrownBy(() -> assets.create(owner, "矢量",
                new AssetUpload("image/svg+xml", "logo.svg", AssetFixture.svg())))
                .isInstanceOf(InvalidAssetException.class)
                .hasMessageContaining("unsupported content type");
    }

    /** 把脚本改名成 .png 并声明成 image/png：文件头对不上，拒收。 */
    @Test
    void contentThatDoesNotMatchItsDeclaredTypeIsRejected() {
        assertThatThrownBy(() -> assets.create(owner, "伪装",
                new AssetUpload("image/png", "evil.png", AssetFixture.svg())))
                .isInstanceOf(InvalidAssetException.class)
                .hasMessageContaining("content does not match");
    }

    /** 真的是 JPEG 却声明成 PNG：同样拒收——不猜、不纠正。 */
    @Test
    void declaringTheWrongImageTypeIsAlsoRejected() {
        assertThatThrownBy(() -> assets.create(owner, "错标",
                new AssetUpload("image/png", "a.png", AssetFixture.jpeg())))
                .isInstanceOf(InvalidAssetException.class)
                .hasMessageContaining("content does not match");
    }

    @Test
    void everyAllowedImageTypeIsAcceptedAndClassifiedAsAnImage() {
        assertThat(assets.create(owner, "png", new AssetUpload("image/png", "a.png", AssetFixture.png())).kind())
                .isEqualTo(AssetKind.IMAGE);
        assertThat(assets.create(owner, "jpeg", new AssetUpload("image/jpeg", "a.jpg", AssetFixture.jpeg())).kind())
                .isEqualTo(AssetKind.IMAGE);
        assertThat(assets.create(owner, "gif", new AssetUpload("image/gif", "a.gif", AssetFixture.gif())).kind())
                .isEqualTo(AssetKind.IMAGE);
        assertThat(assets.create(owner, "webp", new AssetUpload("image/webp", "a.webp", AssetFixture.webp())).kind())
                .isEqualTo(AssetKind.IMAGE);
    }

    @Test
    void videoIsAcceptedAndClassifiedAsVideo() {
        assertThat(assets.create(owner, "mp4", new AssetUpload("video/mp4", "a.mp4", AssetFixture.mp4())).kind())
                .isEqualTo(AssetKind.VIDEO);
    }

    /** 内容类型带参数（浏览器常这么发）时按主类型判定，存下来的是规范化后的值。 */
    @Test
    void aContentTypeWithParametersIsNormalised() {
        AssetView view = assets.create(owner, "png",
                new AssetUpload("image/png; charset=binary", "a.png", AssetFixture.png()));

        assertThat(view.contentType()).isEqualTo("image/png");
    }

    @Test
    void aBlankNameIsRejected() {
        assertThatThrownBy(() -> assets.create(owner, "  ",
                new AssetUpload("image/png", "a.png", AssetFixture.png())))
                .isInstanceOf(InvalidAssetException.class);
    }

    /** 原始文件名只是元数据，绝不参与存储键；穿越串照样存得下，但键里不会出现它。 */
    @Test
    void theOriginalFileNameNeverReachesTheStorageKey() {
        AssetView view = assets.create(owner, "穿越",
                new AssetUpload("image/png", "../../etc/passwd", AssetFixture.png()));

        String key = assets.versions(owner, view.id()).get(0).storageKey();
        assertThat(key).doesNotContain("..").doesNotContain("passwd");
        assertThat(key).contains(owner.tenantId().value().toString()).contains(view.id().toString());
    }
}
