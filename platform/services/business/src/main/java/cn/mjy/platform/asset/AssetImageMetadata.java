package cn.mjy.platform.asset;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Set;

/**
 * 图片元数据剥离（ADR 0019 决定 10）。EXIF 里可能有 GPS 坐标、设备序列号与拍摄时间，
 * 是<b>个人数据</b>，而资产的字节会原样发给每一个作答者，因此在<b>落盘之前</b>剥掉。
 *
 * <p><b>从不解析像素。</b>剥元数据是按容器结构做删除，不是解码重编码：本类只在
 * 段／块的边界上跳着走，认到该删的就跳过去。重编码会掉画质、会改文件大小，
 * 还会引入图像解码器这一整片攻击面（解码恶意图像正是图片库 CVE 的主产区）。
 *
 * <p><b>结构不认识就整件拒收</b>，不"尽力而为地放过去"：长度越界、提前截断一律抛
 * {@link InvalidAssetException}。放过去等于把"已剥离"这句话变成谎话，而调用方看不出来。
 *
 * <p>音视频不剥（已知限制 11）：MP4/WebM 的元数据在 box/element 里，
 * 改写它们需要真正的容器改写器，风险与收益都与图片不同。
 */
final class AssetImageMetadata {

    /** JPEG 里要丢掉的应用段：APP1（Exif/XMP）到 APP15，外加注释段 COM。 */
    private static final int APP0 = 0xe0;
    private static final int APP1 = 0xe1;
    private static final int APP15 = 0xef;
    private static final int COMMENT = 0xfe;
    private static final int START_OF_SCAN = 0xda;
    private static final int END_OF_IMAGE = 0xd9;
    private static final int MARKER = 0xff;
    private static final int START_OF_IMAGE = 0xd8;

    /** PNG 里要丢掉的块：EXIF、三种文本块与时间戳。 */
    private static final Set<String> PNG_DROPPED = Set.of("eXIf", "tEXt", "iTXt", "zTXt", "tIME");
    private static final int PNG_SIGNATURE_BYTES = 8;
    private static final int PNG_CHUNK_OVERHEAD = 12;

    /** WebP（RIFF 扩展容器）里要丢掉的块。 */
    private static final Set<String> WEBP_DROPPED = Set.of("EXIF", "XMP ");
    private static final int RIFF_HEADER_BYTES = 12;
    private static final int RIFF_CHUNK_HEADER_BYTES = 8;

    private AssetImageMetadata() {
    }

    /**
     * 剥掉可剥的元数据。类型以<b>嗅探结果</b>为准（调用方须先过 {@link AssetContentTypes}）。
     *
     * @return 剥完的字节；这一类不需要剥时返回原数组本身
     * @throws InvalidAssetException 容器结构解析不下去
     */
    static byte[] strip(String contentType, byte[] content) {
        return switch (contentType) {
            case "image/jpeg" -> stripJpeg(content);
            case "image/png" -> stripPng(content);
            case "image/webp" -> stripWebp(content);
            default -> content;
        };
    }

    /**
     * JPEG：SOI 之后是一串 {@code FF <marker> <2 字节长度> <载荷>}，直到 SOS；
     * SOS 之后是熵编码数据，原样抄到结尾。
     */
    private static byte[] stripJpeg(byte[] content) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(content.length);
        if (content.length < 4 || (content[0] & 0xff) != MARKER || (content[1] & 0xff) != START_OF_IMAGE) {
            throw malformed("jpeg");
        }
        out.write(MARKER);
        out.write(START_OF_IMAGE);
        int at = 2;
        while (at + 1 < content.length) {
            if ((content[at] & 0xff) != MARKER) {
                throw malformed("jpeg");
            }
            int marker = content[at + 1] & 0xff;
            if (marker == END_OF_IMAGE) {
                out.write(MARKER);
                out.write(END_OF_IMAGE);
                return out.toByteArray();
            }
            if (at + 3 >= content.length) {
                throw malformed("jpeg");
            }
            int length = ((content[at + 2] & 0xff) << 8) | (content[at + 3] & 0xff);
            if (length < 2 || at + 2 + length > content.length) {
                throw malformed("jpeg");
            }
            if (!isDroppedJpegSegment(marker)) {
                out.write(content, at, 2 + length);
            }
            at += 2 + length;
            if (marker == START_OF_SCAN) {
                // 扫描数据没有长度字段，一路抄到文件尾（含结尾的 EOI）。
                out.write(content, at, content.length - at);
                return requireEndOfImage(out.toByteArray());
            }
        }
        throw malformed("jpeg");
    }

    /** APP1…APP15 与 COM 全丢；APP0（JFIF）留着——它是容器本身的一部分，不含个人数据。 */
    private static boolean isDroppedJpegSegment(int marker) {
        return marker == COMMENT || (marker > APP0 && marker <= APP15) || marker == APP1;
    }

    private static byte[] requireEndOfImage(byte[] bytes) {
        int length = bytes.length;
        if (length < 2 || (bytes[length - 2] & 0xff) != MARKER || (bytes[length - 1] & 0xff) != END_OF_IMAGE) {
            throw malformed("jpeg");
        }
        return bytes;
    }

    /** PNG：8 字节签名之后是一串 {@code <4 字节长度> <4 字节类型> <载荷> <4 字节 CRC>}。 */
    private static byte[] stripPng(byte[] content) {
        if (content.length < PNG_SIGNATURE_BYTES) {
            throw malformed("png");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream(content.length);
        out.write(content, 0, PNG_SIGNATURE_BYTES);
        int at = PNG_SIGNATURE_BYTES;
        while (at < content.length) {
            if (at + PNG_CHUNK_OVERHEAD > content.length) {
                throw malformed("png");
            }
            long length = bigEndian(content, at);
            // 整块删掉时 CRC 跟着走，因此不必重算；但长度必须落在文件里。
            if (length < 0 || at + PNG_CHUNK_OVERHEAD + length > content.length) {
                throw malformed("png");
            }
            String type = ascii(content, at + 4, 4);
            int span = (int) (PNG_CHUNK_OVERHEAD + length);
            if (!PNG_DROPPED.contains(type)) {
                out.write(content, at, span);
            }
            at += span;
        }
        return out.toByteArray();
    }

    /**
     * WebP：{@code RIFF <4 字节小端长度> WEBP} 之后是一串
     * {@code <4 字节类型> <4 字节小端长度> <载荷>}（载荷补到偶数长度）。
     * 删块之后<b>必须改写 RIFF 的长度字段</b>，否则解析器会读到文件外。
     */
    private static byte[] stripWebp(byte[] content) {
        if (content.length < RIFF_HEADER_BYTES) {
            throw malformed("webp");
        }
        ByteArrayOutputStream body = new ByteArrayOutputStream(content.length);
        body.write(content, 8, 4);
        int at = RIFF_HEADER_BYTES;
        while (at < content.length) {
            if (at + RIFF_CHUNK_HEADER_BYTES > content.length) {
                throw malformed("webp");
            }
            long length = littleEndian(content, at + 4);
            long padded = length + (length % 2);
            if (length < 0 || at + RIFF_CHUNK_HEADER_BYTES + padded > content.length) {
                throw malformed("webp");
            }
            String type = ascii(content, at, 4);
            int span = (int) (RIFF_CHUNK_HEADER_BYTES + padded);
            if (!WEBP_DROPPED.contains(type)) {
                body.write(content, at, span);
            }
            at += span;
        }
        byte[] payload = body.toByteArray();
        ByteArrayOutputStream out = new ByteArrayOutputStream(payload.length + 8);
        out.write(content, 0, 4);
        out.writeBytes(toLittleEndian(payload.length));
        out.writeBytes(payload);
        return out.toByteArray();
    }

    private static long bigEndian(byte[] bytes, int at) {
        return ((long) (bytes[at] & 0xff) << 24) | ((bytes[at + 1] & 0xff) << 16)
                | ((bytes[at + 2] & 0xff) << 8) | (bytes[at + 3] & 0xff);
    }

    private static long littleEndian(byte[] bytes, int at) {
        return (bytes[at] & 0xffL) | ((bytes[at + 1] & 0xffL) << 8)
                | ((bytes[at + 2] & 0xffL) << 16) | ((bytes[at + 3] & 0xffL) << 24);
    }

    private static byte[] toLittleEndian(int value) {
        return new byte[] {(byte) value, (byte) (value >>> 8), (byte) (value >>> 16), (byte) (value >>> 24)};
    }

    private static String ascii(byte[] bytes, int at, int length) {
        return new String(bytes, at, length, StandardCharsets.US_ASCII);
    }

    private static InvalidAssetException malformed(String container) {
        return new InvalidAssetException("the uploaded " + container
                + " could not be parsed well enough to strip its metadata");
    }
}
