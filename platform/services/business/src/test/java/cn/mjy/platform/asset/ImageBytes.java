package cn.mjy.platform.asset;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;

/**
 * 构造带元数据的最小图片字节，供剥离用例使用（ADR 0019 决定 10）。
 *
 * <p>刻意<b>不</b>用图像库生成：闸门与剥离器都只在容器的段/块边界上走，从不解析像素，
 * 用例也应当在同一个抽象层上说话。字节全部是手工拼的容器结构。
 */
final class ImageBytes {

    /** JPEG 里那段 GPS：只要它还在，剥离就没做到。 */
    static final String GPS_MARKER = "GPS 31.23,121.47";

    /** PNG 文本块里的设备信息。 */
    static final String CAMERA_MARKER = "Camera MJY-1";

    private ImageBytes() {
    }

    /** 结构完整、<b>不带任何可剥元数据</b>的最小 PNG。 */
    static byte[] minimalPng() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a});
        chunk(out, "IHDR", new byte[13]);
        chunk(out, "IDAT", new byte[] {0x78, (byte) 0x9c, 0x01, 0x00});
        chunk(out, "IEND", new byte[0]);
        return out.toByteArray();
    }

    /** 结构完整、不带 APP1 的最小 JPEG。 */
    static byte[] minimalJpeg() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0xff, (byte) 0xd8});
        segment(out, 0xe0, concat("JFIF\u0000".getBytes(StandardCharsets.US_ASCII),
                new byte[] {1, 1, 0, 0, 1, 0, 1, 0, 0}));
        segment(out, 0xda, new byte[] {1, 1, 0, 0, 63, 0});
        out.writeBytes(new byte[] {0x11, 0x22, 0x33, 0x44});
        out.writeBytes(new byte[] {(byte) 0xff, (byte) 0xd9});
        return out.toByteArray();
    }

    /** 结构完整、不带 EXIF 的最小 WebP。 */
    static byte[] minimalWebp() {
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        body.writeBytes("WEBP".getBytes(StandardCharsets.US_ASCII));
        riffChunk(body, "VP8 ", new byte[] {0x10, 0x20, 0x30, 0x40});
        byte[] payload = body.toByteArray();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes("RIFF".getBytes(StandardCharsets.US_ASCII));
        out.writeBytes(littleEndian(payload.length));
        out.writeBytes(payload);
        return out.toByteArray();
    }

    /** JPEG：SOI ＋ APP0(JFIF) ＋ APP1(Exif，含 GPS) ＋ 量化表 ＋ SOS ＋ 扫描数据 ＋ EOI。 */
    static byte[] jpegWithExif() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0xff, (byte) 0xd8});
        segment(out, 0xe0, concat("JFIF\u0000".getBytes(StandardCharsets.US_ASCII),
                new byte[] {1, 1, 0, 0, 1, 0, 1, 0, 0}));
        segment(out, 0xe1, concat("Exif\u0000\u0000".getBytes(StandardCharsets.US_ASCII),
                GPS_MARKER.getBytes(StandardCharsets.US_ASCII)));
        segment(out, 0xdb, new byte[64]);
        segment(out, 0xda, new byte[] {1, 1, 0, 0, 63, 0});
        out.writeBytes(new byte[] {0x11, 0x22, 0x33, 0x44});
        out.writeBytes(new byte[] {(byte) 0xff, (byte) 0xd9});
        return out.toByteArray();
    }

    /** 段长度指到文件外：结构不认识就整件拒收，不「尽力而为地放过去」。 */
    static byte[] jpegWithAtruncatedSegment() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0xff, (byte) 0xd8});
        out.writeBytes(new byte[] {(byte) 0xff, (byte) 0xe1, 0x40, 0x00});
        out.writeBytes("Exif\u0000\u0000".getBytes(StandardCharsets.US_ASCII));
        return out.toByteArray();
    }

    /** PNG：签名 ＋ IHDR ＋ eXIf(GPS) ＋ tEXt(设备) ＋ IDAT ＋ IEND。 */
    static byte[] pngWithMetadata() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a});
        chunk(out, "IHDR", new byte[13]);
        chunk(out, "eXIf", GPS_MARKER.getBytes(StandardCharsets.US_ASCII));
        chunk(out, "tEXt", CAMERA_MARKER.getBytes(StandardCharsets.US_ASCII));
        chunk(out, "IDAT", new byte[] {0x78, (byte) 0x9c, 0x01, 0x00});
        chunk(out, "IEND", new byte[0]);
        return out.toByteArray();
    }

    /** 块长度指到文件外。 */
    static byte[] pngWithAtruncatedChunk() {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(new byte[] {(byte) 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a});
        out.writeBytes(new byte[] {0x7f, (byte) 0xff, (byte) 0xff, (byte) 0xff});
        out.writeBytes("IHDR".getBytes(StandardCharsets.US_ASCII));
        return out.toByteArray();
    }

    /** 扩展 WebP：VP8X ＋ EXIF(GPS) ＋ VP8 图像数据。 */
    static byte[] webpWithExif() {
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        body.writeBytes("WEBP".getBytes(StandardCharsets.US_ASCII));
        riffChunk(body, "VP8X", new byte[10]);
        riffChunk(body, "EXIF", GPS_MARKER.getBytes(StandardCharsets.US_ASCII));
        riffChunk(body, "VP8 ", new byte[] {0x10, 0x20, 0x30, 0x40});
        byte[] payload = body.toByteArray();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes("RIFF".getBytes(StandardCharsets.US_ASCII));
        out.writeBytes(littleEndian(payload.length));
        out.writeBytes(payload);
        return out.toByteArray();
    }

    /** GIF 不带 EXIF：剥离器该原样放行。 */
    static byte[] gif() {
        byte[] bytes = new byte[64];
        System.arraycopy("GIF89a".getBytes(StandardCharsets.US_ASCII), 0, bytes, 0, 6);
        return bytes;
    }

    private static void segment(ByteArrayOutputStream out, int marker, byte[] payload) {
        out.write(0xff);
        out.write(marker);
        int length = payload.length + 2;
        out.write((length >>> 8) & 0xff);
        out.write(length & 0xff);
        out.writeBytes(payload);
    }

    private static void chunk(ByteArrayOutputStream out, String type, byte[] payload) {
        out.writeBytes(bigEndian(payload.length));
        out.writeBytes(type.getBytes(StandardCharsets.US_ASCII));
        out.writeBytes(payload);
        // CRC 不参与剥离判定（整块删掉时它跟着走），用例里放固定值即可。
        out.writeBytes(new byte[] {0x0a, 0x0b, 0x0c, 0x0d});
    }

    private static void riffChunk(ByteArrayOutputStream out, String fourCc, byte[] payload) {
        out.writeBytes(fourCc.getBytes(StandardCharsets.US_ASCII));
        out.writeBytes(littleEndian(payload.length));
        out.writeBytes(payload);
        if (payload.length % 2 == 1) {
            out.write(0);
        }
    }

    private static byte[] bigEndian(int value) {
        return new byte[] {(byte) (value >>> 24), (byte) (value >>> 16), (byte) (value >>> 8), (byte) value};
    }

    private static byte[] littleEndian(int value) {
        return new byte[] {(byte) value, (byte) (value >>> 8), (byte) (value >>> 16), (byte) (value >>> 24)};
    }

    private static byte[] concat(byte[] first, byte[] second) {
        byte[] joined = new byte[first.length + second.length];
        System.arraycopy(first, 0, joined, 0, first.length);
        System.arraycopy(second, 0, joined, first.length, second.length);
        return joined;
    }
}
