package cn.mjy.platform.asset;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/**
 * 图片元数据剥离（ADR 0019 决定 10）。EXIF 里可能有 GPS 与设备信息，是个人数据，
 * 而资产的字节会原样发给作答者，所以在<b>落盘之前</b>剥掉。
 *
 * <p>本类是纯函数用例；"入库存的是剥完那份字节"由 {@link AssetExifStripTest} 从服务层钉住。
 */
class AssetImageMetadataTest {

    @Test
    void jpegLosesItsExifSegmentButKeepsTheImage() {
        byte[] original = ImageBytes.jpegWithExif();

        byte[] stripped = AssetImageMetadata.strip("image/jpeg", original);

        assertThat(ascii(stripped)).doesNotContain(ImageBytes.GPS_MARKER).doesNotContain("Exif");
        assertThat(stripped.length).isLessThan(original.length);
        // SOI / EOI 与扫描数据必须原样留着：剥的是元数据，不是图。
        assertThat(stripped[0] & 0xff).isEqualTo(0xff);
        assertThat(stripped[1] & 0xff).isEqualTo(0xd8);
        assertThat(stripped[stripped.length - 2] & 0xff).isEqualTo(0xff);
        assertThat(stripped[stripped.length - 1] & 0xff).isEqualTo(0xd9);
        assertThat(ascii(stripped)).contains("JFIF");
    }

    /** 剥完还得是同一种东西：剥离器自己不能把一张 JPEG 变成别的。 */
    @Test
    void astrippedJpegStillPassesTheMagicByteGate() {
        byte[] stripped = AssetImageMetadata.strip("image/jpeg", ImageBytes.jpegWithExif());

        assertThat(AssetContentTypes.contentMatches("image/jpeg", stripped)).isTrue();
    }

    @Test
    void pngLosesItsExifAndTextChunks() {
        byte[] stripped = AssetImageMetadata.strip("image/png", ImageBytes.pngWithMetadata());

        assertThat(ascii(stripped))
                .doesNotContain(ImageBytes.GPS_MARKER)
                .doesNotContain(ImageBytes.CAMERA_MARKER)
                .doesNotContain("eXIf")
                .doesNotContain("tEXt")
                .contains("IHDR").contains("IDAT").contains("IEND");
        assertThat(AssetContentTypes.contentMatches("image/png", stripped)).isTrue();
    }

    @Test
    void webpLosesItsExifChunkAndTheRiffLengthIsRewritten() {
        byte[] stripped = AssetImageMetadata.strip("image/webp", ImageBytes.webpWithExif());

        assertThat(ascii(stripped)).doesNotContain(ImageBytes.GPS_MARKER).doesNotContain("EXIF");
        assertThat(ascii(stripped)).contains("VP8X").contains("VP8 ");
        assertThat(AssetContentTypes.contentMatches("image/webp", stripped)).isTrue();
        // RIFF 的长度字段必须跟着缩，否则解析器会读到文件外。
        assertThat(riffLength(stripped)).isEqualTo(stripped.length - 8);
    }

    @Test
    void gifCarriesNoExifAndIsLeftAlone() {
        byte[] original = ImageBytes.gif();

        assertThat(AssetImageMetadata.strip("image/gif", original)).isEqualTo(original);
    }

    /** 音视频不剥（已知限制 11）：原样返回，不假装做过。 */
    @Test
    void audioAndVideoAreLeftAlone() {
        byte[] audio = AssetFixture.webm();

        assertThat(AssetImageMetadata.strip("audio/webm", audio)).isEqualTo(audio);
        assertThat(AssetImageMetadata.strip("video/mp4", AssetFixture.mp4())).isEqualTo(AssetFixture.mp4());
    }

    /** 结构不认识就整件拒收：放过去等于把"已剥离"这句话变成谎话。 */
    @Test
    void ajpegWhoseSegmentRunsPastTheEndIsRefused() {
        assertThatThrownBy(() -> AssetImageMetadata.strip("image/jpeg", ImageBytes.jpegWithAtruncatedSegment()))
                .isInstanceOf(InvalidAssetException.class);
    }

    @Test
    void apngWhoseChunkRunsPastTheEndIsRefused() {
        assertThatThrownBy(() -> AssetImageMetadata.strip("image/png", ImageBytes.pngWithAtruncatedChunk()))
                .isInstanceOf(InvalidAssetException.class);
    }

    /** 剥到一张图都不剩也是拒收：那说明输入根本不是这个容器。 */
    @Test
    void ajpegWithNoEndOfImageMarkerIsRefused() {
        byte[] truncated = new byte[] {(byte) 0xff, (byte) 0xd8, (byte) 0xff, (byte) 0xdb, 0x00, 0x04, 0x00, 0x00};

        assertThatThrownBy(() -> AssetImageMetadata.strip("image/jpeg", truncated))
                .isInstanceOf(InvalidAssetException.class);
    }

    private static String ascii(byte[] bytes) {
        return new String(bytes, StandardCharsets.ISO_8859_1);
    }

    private static int riffLength(byte[] bytes) {
        return (bytes[4] & 0xff) | ((bytes[5] & 0xff) << 8) | ((bytes[6] & 0xff) << 16) | ((bytes[7] & 0xff) << 24);
    }
}
