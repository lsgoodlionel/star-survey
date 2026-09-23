package cn.mjy.platform.delivery;

import java.util.ArrayList;
import java.util.List;

/**
 * 二维码的码字层（ISO/IEC 18004）：字节模式的位流、填充、里德-所罗门纠错码字与分块交织。
 * 只实现纠错等级 M 与版本 1–10（最长 213 字节，足够放下带签名参数的作答链接），
 * 不引第三方依赖——JDK 里没有二维码编码器，整段自己实现，正确性由 {@code QrCodeTest} 的独立解码器验证。
 */
final class QrCodewords {

    /** 字节模式的模式指示符。 */
    private static final int BYTE_MODE = 0b0100;
    /** 纠错等级 M 的填充码字，按规范交替使用。 */
    private static final int PAD_EVEN = 0xEC;
    private static final int PAD_ODD = 0x11;
    static final int MIN_VERSION = 1;
    static final int MAX_VERSION = 10;

    /**
     * 每个版本（纠错等级 M）的分块参数：
     * {每块纠错码字数, 第一组块数, 第一组每块数据码字数, 第二组块数, 第二组每块数据码字数}。
     */
    private static final int[][] LAYOUT = {
        {},
        {10, 1, 16, 0, 0},
        {16, 1, 28, 0, 0},
        {26, 1, 44, 0, 0},
        {18, 2, 32, 0, 0},
        {24, 2, 43, 0, 0},
        {16, 4, 27, 0, 0},
        {18, 4, 31, 0, 0},
        {22, 2, 38, 2, 39},
        {22, 3, 36, 2, 37},
        {26, 4, 43, 1, 44},
    };

    private QrCodewords() {
    }

    /** 该版本的数据码字总数（不含纠错码字）。 */
    static int dataCapacity(int version) {
        int[] layout = layoutOf(version);
        return layout[1] * layout[2] + layout[3] * layout[4];
    }

    /** 该版本字节模式能放下的最大字节数（扣掉模式指示符与字符计数）。 */
    static int maxPayloadBytes(int version) {
        return (dataCapacity(version) * 8 - 4 - countBits(version)) / 8;
    }

    /** 能放下该长度的最小版本。 */
    static int smallestVersionFor(int payloadLength) {
        for (int version = MIN_VERSION; version <= MAX_VERSION; version++) {
            if (payloadLength <= maxPayloadBytes(version)) {
                return version;
            }
        }
        throw new IllegalArgumentException("payload of " + payloadLength
                + " bytes exceeds QR version " + MAX_VERSION + " (max " + maxPayloadBytes(MAX_VERSION) + " bytes)");
    }

    /** 每个分块的 {数据码字数, 纠错码字数}，顺序与交织顺序一致。 */
    static List<int[]> blockLayout(int version) {
        int[] layout = layoutOf(version);
        List<int[]> blocks = new ArrayList<>();
        for (int i = 0; i < layout[1]; i++) {
            blocks.add(new int[] {layout[2], layout[0]});
        }
        for (int i = 0; i < layout[3]; i++) {
            blocks.add(new int[] {layout[4], layout[0]});
        }
        return List.copyOf(blocks);
    }

    /** 模式指示符 + 字符计数 + 原文 + 终止符 + 补零 + 交替填充，填满该版本的数据码字。 */
    static byte[] dataCodewords(byte[] payload, int version) {
        int capacity = dataCapacity(version);
        if (payload.length > maxPayloadBytes(version)) {
            throw new IllegalArgumentException("payload does not fit version " + version);
        }
        BitWriter bits = new BitWriter(capacity);
        bits.write(BYTE_MODE, 4);
        bits.write(payload.length, countBits(version));
        for (byte b : payload) {
            bits.write(b & 0xFF, 8);
        }
        bits.write(0, Math.min(4, capacity * 8 - bits.length()));
        bits.write(0, (8 - bits.length() % 8) % 8);
        byte[] codewords = bits.toBytes();
        for (int i = bits.length() / 8; i < capacity; i++) {
            codewords[i] = (byte) (((i - bits.length() / 8) % 2 == 0) ? PAD_EVEN : PAD_ODD);
        }
        return codewords;
    }

    /** 按规范给数据码字分块、逐块算纠错码字，再按"各块第 i 个码字"的顺序交织成最终码字序列。 */
    static byte[] interleave(byte[] data, int version) {
        List<int[]> layout = blockLayout(version);
        List<byte[]> dataBlocks = new ArrayList<>();
        List<byte[]> ecBlocks = new ArrayList<>();
        int position = 0;
        for (int[] block : layout) {
            byte[] chunk = new byte[block[0]];
            System.arraycopy(data, position, chunk, 0, block[0]);
            position += block[0];
            dataBlocks.add(chunk);
            ecBlocks.add(errorCorrection(chunk, block[1]));
        }
        byte[] result = new byte[totalCodewords(version)];
        int out = 0;
        int longest = layout.stream().mapToInt(block -> block[0]).max().orElseThrow();
        for (int i = 0; i < longest; i++) {
            for (int b = 0; b < dataBlocks.size(); b++) {
                if (i < dataBlocks.get(b).length) {
                    result[out++] = dataBlocks.get(b)[i];
                }
            }
        }
        for (int i = 0; i < layout.get(0)[1]; i++) {
            for (byte[] ec : ecBlocks) {
                result[out++] = ec[i];
            }
        }
        return result;
    }

    static int totalCodewords(int version) {
        int[] layout = layoutOf(version);
        return dataCapacity(version) + layout[0] * (layout[1] + layout[3]);
    }

    /** 版本 1–9 的字符计数为 8 位，10 起为 16 位（字节模式）。 */
    private static int countBits(int version) {
        return version <= 9 ? 8 : 16;
    }

    private static int[] layoutOf(int version) {
        if (version < MIN_VERSION || version > MAX_VERSION) {
            throw new IllegalArgumentException("unsupported QR version " + version);
        }
        return LAYOUT[version];
    }

    // --- 里德-所罗门（GF(256)，本原多项式 0x11D） ---

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

    static byte[] errorCorrection(byte[] data, int ecLength) {
        int[] generator = generatorPolynomial(ecLength);
        int[] remainder = new int[ecLength];
        for (byte b : data) {
            int factor = (b & 0xFF) ^ remainder[0];
            System.arraycopy(remainder, 1, remainder, 0, ecLength - 1);
            remainder[ecLength - 1] = 0;
            for (int i = 0; i < ecLength; i++) {
                remainder[i] ^= multiply(generator[i], factor);
            }
        }
        byte[] result = new byte[ecLength];
        for (int i = 0; i < ecLength; i++) {
            result[i] = (byte) remainder[i];
        }
        return result;
    }

    /** (x - α^0)(x - α^1)…(x - α^(n-1)) 的系数，最高次项省略（恒为 1）。 */
    private static int[] generatorPolynomial(int degree) {
        int[] result = new int[degree];
        result[degree - 1] = 1;
        int root = 1;
        for (int i = 0; i < degree; i++) {
            for (int j = 0; j < degree; j++) {
                result[j] = multiply(result[j], root);
                if (j + 1 < degree) {
                    result[j] ^= result[j + 1];
                }
            }
            root = multiply(root, 2);
        }
        return result;
    }

    private static int multiply(int a, int b) {
        return a == 0 || b == 0 ? 0 : EXP[LOG[a] + LOG[b]];
    }

    /** 按位写入固定容量的缓冲区。 */
    private static final class BitWriter {

        private final byte[] buffer;
        private int length;

        BitWriter(int capacityBytes) {
            this.buffer = new byte[capacityBytes];
        }

        void write(int value, int bits) {
            for (int i = bits - 1; i >= 0; i--) {
                if (((value >>> i) & 1) != 0) {
                    buffer[length / 8] |= (byte) (0x80 >>> (length % 8));
                }
                length++;
            }
        }

        int length() {
            return length;
        }

        byte[] toBytes() {
            return buffer.clone();
        }
    }
}
