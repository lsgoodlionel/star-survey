package cn.mjy.platform.delivery;

import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.List;
import javax.imageio.ImageIO;

/**
 * 测试专用的二维码解码器：只读矩阵，自己判功能模块、自己去掩码、自己走 Z 字形排布、自己做 GF(256) 伴随式验算，
 * 不调用被测编码器的任何排布或掩码代码（只借用分块表，因为那是规范里的固定数据，不是逻辑）。
 * 编码器写错位置、掩码选错、纠错算错，这里都会暴露。
 */
final class QrMatrixDecoder {

    /** 对齐图案中心坐标（ISO/IEC 18004 附录 E），版本 1–10。 */
    private static final int[][] ALIGNMENT = {
        {}, {}, {6, 18}, {6, 22}, {6, 26}, {6, 30}, {6, 34}, {6, 22, 38}, {6, 24, 42}, {6, 26, 46}, {6, 28, 50}
    };

    private QrMatrixDecoder() {
    }

    static boolean[][] modules(QrCode qr) {
        boolean[][] modules = new boolean[qr.size()][qr.size()];
        for (int y = 0; y < qr.size(); y++) {
            for (int x = 0; x < qr.size(); x++) {
                modules[y][x] = qr.dark(x, y);
            }
        }
        return modules;
    }

    /** 主副本：(0..5,8)、(7,8)、(8,8)、(8,7)、(8,5..0)，共 15 位，最高位在前。 */
    static int primaryFormatBits(QrCode qr) {
        int bits = 0;
        for (int i = 0; i <= 5; i++) {
            bits = (bits << 1) | bit(qr, i, 8);
        }
        bits = (bits << 1) | bit(qr, 7, 8);
        bits = (bits << 1) | bit(qr, 8, 8);
        bits = (bits << 1) | bit(qr, 8, 7);
        for (int i = 5; i >= 0; i--) {
            bits = (bits << 1) | bit(qr, 8, i);
        }
        return bits;
    }

    /** 副副本：(8, size-1 .. size-7) 与 (size-8 .. size-1, 8)。 */
    static int secondaryFormatBits(QrCode qr) {
        int size = qr.size();
        int bits = 0;
        for (int i = 0; i < 7; i++) {
            bits = (bits << 1) | bit(qr, 8, size - 1 - i);
        }
        for (int i = 7; i >= 0; i--) {
            bits = (bits << 1) | bit(qr, size - 1 - i, 8);
        }
        return bits;
    }

    /** value 除以生成多项式的余数（二进制多项式除法），degreeOffset 为校验位数。 */
    static int bchRemainder(int value, int generator, int degreeOffset) {
        int remainder = value;
        int generatorDegree = 31 - Integer.numberOfLeadingZeros(generator);
        while (31 - Integer.numberOfLeadingZeros(remainder) >= generatorDegree && remainder != 0) {
            int shift = (31 - Integer.numberOfLeadingZeros(remainder)) - generatorDegree;
            remainder ^= generator << shift;
        }
        return remainder & ((1 << degreeOffset) - 1);
    }

    static byte[] decode(QrCode qr) {
        int version = version(qr);
        List<byte[]> blocks = deinterleave(readCodewords(qr), version);
        List<int[]> layout = QrCodewords.blockLayout(version);
        java.io.ByteArrayOutputStream data = new java.io.ByteArrayOutputStream();
        for (int i = 0; i < blocks.size(); i++) {
            data.write(blocks.get(i), 0, layout.get(i)[0]);
        }
        return parse(data.toByteArray(), version);
    }

    static List<int[]> blockSyndromes(QrCode qr) {
        int version = version(qr);
        List<int[]> syndromes = new ArrayList<>();
        int index = 0;
        for (byte[] block : deinterleave(readCodewords(qr), version)) {
            int ecLength = QrCodewords.blockLayout(version).get(index++)[1];
            int[] syndrome = new int[ecLength];
            for (int i = 0; i < ecLength; i++) {
                int value = 0;
                // 二维码的生成多项式以 α^0…α^(t-1) 为根，因此伴随式在这几个点上必须全为零。
                int x = exp(i);
                for (byte codeword : block) {
                    value = multiply(value, x) ^ (codeword & 0xFF);
                }
                syndrome[i] = value;
            }
            syndromes.add(syndrome);
        }
        return syndromes;
    }

    static boolean[][] readPngModules(byte[] png, int moduleSize, int quiet, int size) {
        BufferedImage image;
        try {
            image = ImageIO.read(new ByteArrayInputStream(png));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        if (image == null) {
            throw new IllegalArgumentException("not a readable PNG");
        }
        boolean[][] modules = new boolean[size][size];
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) {
                int rgb = image.getRGB((quiet + x) * moduleSize + moduleSize / 2,
                        (quiet + y) * moduleSize + moduleSize / 2);
                modules[y][x] = (rgb & 0xFFFFFF) == 0;
            }
        }
        return modules;
    }

    private static byte[] parse(byte[] data, int version) {
        BitReader reader = new BitReader(data);
        int mode = reader.read(4);
        if (mode != 0b0100) {
            throw new IllegalStateException("expected byte mode, got " + mode);
        }
        int length = reader.read(version <= 9 ? 8 : 16);
        byte[] payload = new byte[length];
        for (int i = 0; i < length; i++) {
            payload[i] = (byte) reader.read(8);
        }
        return payload;
    }

    private static List<byte[]> deinterleave(byte[] codewords, int version) {
        List<int[]> layout = QrCodewords.blockLayout(version);
        List<byte[]> blocks = new ArrayList<>();
        for (int[] block : layout) {
            blocks.add(new byte[block[0] + block[1]]);
        }
        int position = 0;
        int longestData = layout.stream().mapToInt(block -> block[0]).max().orElseThrow();
        for (int i = 0; i < longestData; i++) {
            for (int b = 0; b < layout.size(); b++) {
                if (i < layout.get(b)[0]) {
                    blocks.get(b)[i] = codewords[position++];
                }
            }
        }
        int ecLength = layout.get(0)[1];
        for (int i = 0; i < ecLength; i++) {
            for (int b = 0; b < layout.size(); b++) {
                blocks.get(b)[layout.get(b)[0] + i] = codewords[position++];
            }
        }
        return blocks;
    }

    /** 右下角起的 Z 字形读取，跳过第 6 列与全部功能模块，按格式信息里的掩码号去掩码。 */
    private static byte[] readCodewords(QrCode qr) {
        int size = qr.size();
        boolean[][] function = functionModules(size, version(qr));
        int mask = maskOf(qr);
        int total = (countFree(function) / 8);
        byte[] codewords = new byte[total];
        int bitIndex = 0;
        boolean upward = true;
        for (int right = size - 1; right >= 1; right -= 2) {
            if (right == 6) {
                right = 5;
            }
            for (int step = 0; step < size; step++) {
                int y = upward ? size - 1 - step : step;
                for (int c = 0; c < 2; c++) {
                    int x = right - c;
                    if (function[y][x]) {
                        continue;
                    }
                    boolean dark = qr.dark(x, y) ^ maskBit(mask, x, y);
                    if (dark && bitIndex / 8 < total) {
                        codewords[bitIndex / 8] |= (byte) (0x80 >>> (bitIndex % 8));
                    }
                    bitIndex++;
                }
            }
            upward = !upward;
        }
        return codewords;
    }

    private static int maskOf(QrCode qr) {
        int bits = primaryFormatBits(qr) ^ 0x5412;
        return (bits >> 10) & 0b111;
    }

    private static boolean maskBit(int mask, int x, int y) {
        return switch (mask) {
            case 0 -> (x + y) % 2 == 0;
            case 1 -> y % 2 == 0;
            case 2 -> x % 3 == 0;
            case 3 -> (x + y) % 3 == 0;
            case 4 -> (y / 2 + x / 3) % 2 == 0;
            case 5 -> (x * y) % 2 + (x * y) % 3 == 0;
            case 6 -> ((x * y) % 2 + (x * y) % 3) % 2 == 0;
            case 7 -> ((x + y) % 2 + (x * y) % 3) % 2 == 0;
            default -> throw new IllegalStateException("mask " + mask);
        };
    }

    private static boolean[][] functionModules(int size, int version) {
        boolean[][] reserved = new boolean[size][size];
        for (int[] origin : new int[][] {{0, 0}, {size - 7, 0}, {0, size - 7}}) {
            fill(reserved, origin[0] - 1, origin[1] - 1, 9, 9, size);
        }
        for (int i = 0; i < size; i++) {
            reserved[6][i] = true;
            reserved[i][6] = true;
        }
        int[] centres = ALIGNMENT[version];
        for (int cy : centres) {
            for (int cx : centres) {
                boolean overlapsFinder = (cx <= 8 && cy <= 8) || (cx <= 8 && cy >= size - 9)
                        || (cx >= size - 9 && cy <= 8);
                if (!overlapsFinder) {
                    fill(reserved, cx - 2, cy - 2, 5, 5, size);
                }
            }
        }
        // 格式信息主副本占 (0..8, 8) 与 (8, 0..8)（其中第 6 行/列本就是定时图案）。
        for (int i = 0; i <= 8; i++) {
            reserved[8][i] = true;
            reserved[i][8] = true;
        }
        // 副副本各占 8 个模块：(size-8..size-1, 8) 与 (8, size-8..size-1)，后者最外侧那个是恒黑模块。
        for (int i = 0; i < 8; i++) {
            reserved[8][size - 1 - i] = true;
            reserved[size - 1 - i][8] = true;
        }
        if (version >= 7) {
            for (int i = 0; i < 6; i++) {
                for (int j = 0; j < 3; j++) {
                    reserved[i][size - 11 + j] = true;
                    reserved[size - 11 + j][i] = true;
                }
            }
        }
        return reserved;
    }

    private static void fill(boolean[][] reserved, int x0, int y0, int width, int height, int size) {
        for (int y = y0; y < y0 + height; y++) {
            for (int x = x0; x < x0 + width; x++) {
                if (x >= 0 && y >= 0 && x < size && y < size) {
                    reserved[y][x] = true;
                }
            }
        }
    }

    private static int countFree(boolean[][] function) {
        int free = 0;
        for (boolean[] row : function) {
            for (boolean reserved : row) {
                if (!reserved) {
                    free++;
                }
            }
        }
        return free;
    }

    private static int version(QrCode qr) {
        return (qr.size() - 17) / 4;
    }

    private static int bit(QrCode qr, int x, int y) {
        return qr.dark(x, y) ? 1 : 0;
    }

    // --- GF(256)，本原多项式 0x11D ---

    private static final int[] EXP = new int[512];
    private static final int[] LOG = new int[256];

    static {
        int value = 1;
        for (int i = 0; i < 255; i++) {
            EXP[i] = value;
            LOG[value] = i;
            value <<= 1;
            if ((value & 0x100) != 0) {
                value ^= 0x11D;
            }
        }
        for (int i = 255; i < 512; i++) {
            EXP[i] = EXP[i - 255];
        }
    }

    private static int exp(int power) {
        return EXP[power % 255];
    }

    private static int multiply(int a, int b) {
        return a == 0 || b == 0 ? 0 : EXP[LOG[a] + LOG[b]];
    }

    /** 从码字字节流里按位读取。 */
    private static final class BitReader {

        private final byte[] data;
        private int position;

        BitReader(byte[] data) {
            this.data = data;
        }

        int read(int bits) {
            int value = 0;
            for (int i = 0; i < bits; i++) {
                int byteIndex = position / 8;
                int bit = byteIndex < data.length ? (data[byteIndex] >> (7 - position % 8)) & 1 : 0;
                value = (value << 1) | bit;
                position++;
            }
            return value;
        }
    }
}
