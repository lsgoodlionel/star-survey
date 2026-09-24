package cn.mjy.platform.asset;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.shared.TenantContext;
import java.nio.charset.StandardCharsets;
import org.springframework.stereotype.Component;

/** 资产测试夹具：开通租户、造几种真实文件头的字节。全部走公开服务方法。 */
@Component
public class AssetFixture {

    /** 只有 view、没有 edit 的角色：能看资产，不能上传。 */
    public static final String VIEWER_ROLE = "statistics_viewer";

    /** 有 view ＋ edit 的角色。 */
    public static final String EDITOR_ROLE = "project_manager";

    private final AccessFixture access;

    public AssetFixture(AccessFixture access) {
        this.access = access;
    }

    public TenantContext newTenant() {
        return access.newTenant();
    }

    public TenantContext editor(TenantContext owner, String actor) {
        return access.member(owner, actor, EDITOR_ROLE, null);
    }

    public TenantContext viewer(TenantContext owner, String actor) {
        return access.member(owner, actor, VIEWER_ROLE, null);
    }

    /**
     * 最小 PNG：签名 ＋ IHDR ＋ IDAT ＋ IEND，<b>不带任何元数据块</b>。
     *
     * <p>像素内容不在闸门的职责里，<b>容器结构在</b>：剥离器会按块边界走一遍
     * （ADR 0019 决定 10），结构不成立就整件拒收，所以夹具得是真的块流而不是一段填充。
     * 既然没有可剥的东西，剥完与剥前逐字节相同——"上传什么取回什么"的用例因此仍然成立。
     */
    public static byte[] png() {
        return ImageBytes.minimalPng();
    }

    /** 最小 JPEG：SOI ＋ APP0(JFIF) ＋ SOS ＋ 扫描数据 ＋ EOI，不带 APP1。 */
    public static byte[] jpeg() {
        return ImageBytes.minimalJpeg();
    }

    public static byte[] gif() {
        return withTail("GIF89a".getBytes(StandardCharsets.US_ASCII));
    }

    /** 最小 WebP：RIFF/WEBP 容器里一个 VP8 块，不带 EXIF。 */
    public static byte[] webp() {
        return ImageBytes.minimalWebp();
    }

    /** RIFF 容器，但第 9–12 字节是 WAVE：声明成 image/webp 时必须被认出来不是图。 */
    public static byte[] wav() {
        byte[] bytes = new byte[64];
        System.arraycopy("RIFF".getBytes(StandardCharsets.US_ASCII), 0, bytes, 0, 4);
        System.arraycopy("WAVE".getBytes(StandardCharsets.US_ASCII), 0, bytes, 8, 4);
        return bytes;
    }

    /** Matroska / WebM 的 EBML 头；作答者的录音录像走这一种。 */
    public static byte[] webm() {
        return withTail(new byte[] {0x1a, 0x45, (byte) 0xdf, (byte) 0xa3});
    }

    public static byte[] mp4() {
        byte[] bytes = new byte[64];
        System.arraycopy("ftypisom".getBytes(StandardCharsets.US_ASCII), 0, bytes, 4, 8);
        return bytes;
    }

    public static byte[] svg() {
        return "<svg xmlns=\"http://www.w3.org/2000/svg\"><script>alert(1)</script></svg>"
                .getBytes(StandardCharsets.UTF_8);
    }

    private static byte[] withTail(byte[] header) {
        byte[] bytes = new byte[64];
        System.arraycopy(header, 0, bytes, 0, header.length);
        return bytes;
    }
}
