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

    /** 最小 PNG：八字节签名 ＋ 一段填充。内容是不是合法图像不在闸门的职责里，文件头是。 */
    public static byte[] png() {
        return withTail(new byte[] {(byte) 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a});
    }

    public static byte[] jpeg() {
        return withTail(new byte[] {(byte) 0xff, (byte) 0xd8, (byte) 0xff, (byte) 0xe0});
    }

    public static byte[] gif() {
        return withTail("GIF89a".getBytes(StandardCharsets.US_ASCII));
    }

    /** RIFF 容器：第 8 字节起是 WEBP。 */
    public static byte[] webp() {
        byte[] bytes = new byte[64];
        System.arraycopy("RIFF".getBytes(StandardCharsets.US_ASCII), 0, bytes, 0, 4);
        System.arraycopy("WEBP".getBytes(StandardCharsets.US_ASCII), 0, bytes, 8, 4);
        return bytes;
    }

    /** RIFF 容器，但第 9–12 字节是 WAVE：声明成 image/webp 时必须被认出来不是图。 */
    public static byte[] wav() {
        byte[] bytes = new byte[64];
        System.arraycopy("RIFF".getBytes(StandardCharsets.US_ASCII), 0, bytes, 0, 4);
        System.arraycopy("WAVE".getBytes(StandardCharsets.US_ASCII), 0, bytes, 8, 4);
        return bytes;
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
