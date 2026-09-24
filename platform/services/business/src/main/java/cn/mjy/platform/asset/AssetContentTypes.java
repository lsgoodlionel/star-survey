package cn.mjy.platform.asset;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.function.Predicate;

/**
 * 内容类型白名单与文件头（魔数）校验（ADR 0019 决定 3）。
 *
 * <p>两件事：<b>白名单</b>决定"这类东西平台收不收"，<b>魔数</b>决定"它是不是自称的那个东西"。
 * 两道都过了才收，且之后一切（入库、回放的 Content-Type）都以这里认出来的类型为准，
 * 不以调用方声明的为准。
 *
 * <p><b>SVG 不在白名单里，而且永远不该加进来</b>：它是可执行文档（内嵌脚本、外部实体），
 * 在平台域名下回放等于凭空给出一个任意脚本执行点。矢量图请先转成 PNG/WebP。
 * 同理不收 {@code text/*} 与 {@code application/*}。
 */
final class AssetContentTypes {

    /** 魔数至少要看这么多字节，少于此的一律判为内容不符。 */
    private static final int MIN_HEADER_BYTES = 12;

    private static final Map<String, Allowed> ALLOWED = allowList();

    private AssetContentTypes() {
    }

    /** 白名单里的一项：属于哪一类，以及怎么认它的文件头。 */
    private record Allowed(AssetKind kind, Predicate<byte[]> header) {
    }

    /** 去掉参数、小写、去空白；{@code image/png; charset=binary} → {@code image/png}。 */
    static String normalise(String declared) {
        if (declared == null) {
            return "";
        }
        int semicolon = declared.indexOf(';');
        String bare = semicolon < 0 ? declared : declared.substring(0, semicolon);
        return bare.trim().toLowerCase(Locale.ROOT);
    }

    static Optional<AssetKind> kindOf(String contentType) {
        Allowed allowed = ALLOWED.get(contentType);
        return allowed == null ? Optional.empty() : Optional.of(allowed.kind());
    }

    /** 文件头是不是这个类型该有的样子。类型不在白名单里时恒为假。 */
    static boolean contentMatches(String contentType, byte[] content) {
        Allowed allowed = ALLOWED.get(contentType);
        if (allowed == null || content == null || content.length < MIN_HEADER_BYTES) {
            return false;
        }
        return allowed.header().test(content);
    }

    private static Map<String, Allowed> allowList() {
        Map<String, Allowed> types = new LinkedHashMap<>();
        types.put("image/png", new Allowed(AssetKind.IMAGE,
                bytes -> startsWith(bytes, new byte[] {(byte) 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a})));
        types.put("image/jpeg", new Allowed(AssetKind.IMAGE,
                bytes -> startsWith(bytes, new byte[] {(byte) 0xff, (byte) 0xd8, (byte) 0xff})));
        types.put("image/gif", new Allowed(AssetKind.IMAGE,
                bytes -> matchesAscii(bytes, 0, "GIF87a") || matchesAscii(bytes, 0, "GIF89a")));
        types.put("image/webp", new Allowed(AssetKind.IMAGE, AssetContentTypes::isRiff));
        types.put("audio/mpeg", new Allowed(AssetKind.AUDIO, AssetContentTypes::isMpegAudio));
        types.put("audio/wav", new Allowed(AssetKind.AUDIO, AssetContentTypes::isRiff));
        types.put("audio/webm", new Allowed(AssetKind.AUDIO, AssetContentTypes::isMatroska));
        types.put("video/webm", new Allowed(AssetKind.VIDEO, AssetContentTypes::isMatroska));
        types.put("video/mp4", new Allowed(AssetKind.VIDEO, bytes -> matchesAscii(bytes, 4, "ftyp")));
        return Map.copyOf(types);
    }

    /**
     * RIFF 容器（WebP 与 WAV 共用）：第 9–12 字节是 {@code WEBP} 或 {@code WAVE}。
     * 两者都落在这里，是因为区分它们要读更深的块头，而白名单已经把"能不能收"这件事答完了——
     * 真正要挡的是"声明成图片的可执行文件"，不是"把 wav 说成 webp"。
     */
    private static boolean isRiff(byte[] bytes) {
        return matchesAscii(bytes, 0, "RIFF") && (matchesAscii(bytes, 8, "WEBP") || matchesAscii(bytes, 8, "WAVE"));
    }

    /** Matroska / WebM 的 EBML 头。 */
    private static boolean isMatroska(byte[] bytes) {
        return startsWith(bytes, new byte[] {0x1a, 0x45, (byte) 0xdf, (byte) 0xa3});
    }

    /** MP3：带 ID3 标签，或直接是帧同步（11 个 1）。 */
    private static boolean isMpegAudio(byte[] bytes) {
        if (matchesAscii(bytes, 0, "ID3")) {
            return true;
        }
        return (bytes[0] & 0xff) == 0xff && (bytes[1] & 0xe0) == 0xe0;
    }

    private static boolean startsWith(byte[] bytes, byte[] prefix) {
        for (int i = 0; i < prefix.length; i++) {
            if (bytes[i] != prefix[i]) {
                return false;
            }
        }
        return true;
    }

    private static boolean matchesAscii(byte[] bytes, int offset, String text) {
        byte[] expected = text.getBytes(StandardCharsets.US_ASCII);
        if (bytes.length < offset + expected.length) {
            return false;
        }
        for (int i = 0; i < expected.length; i++) {
            if (bytes[offset + i] != expected[i]) {
                return false;
            }
        }
        return true;
    }
}
