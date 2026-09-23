package cn.mjy.platform.delivery;

import java.nio.charset.StandardCharsets;

/**
 * 一个二维码符号（ISO/IEC 18004，字节模式、纠错等级 M、版本 1–10）。
 * 只依赖 JDK：功能图案、数据排布、八种掩码的惩罚评分、格式与版本信息都在这里实现。
 *
 * <p>纠错等级取 M（约 15% 纠错能力）：链接本身不长，M 在"印小一点仍能扫"与"符号别太大"之间是常用折中。
 */
public final class QrCode {

    /** 纠错等级 M 的两位指示符。 */
    private static final int EC_LEVEL_M = 0b00;
    private static final int FORMAT_GENERATOR = 0x537;
    private static final int FORMAT_MASK = 0x5412;
    private static final int VERSION_GENERATOR = 0x1F25;
    private static final int MASK_COUNT = 8;

    /** 对齐图案中心坐标（ISO/IEC 18004 附录 E），版本 1–10。 */
    private static final int[][] ALIGNMENT = {
        {}, {}, {6, 18}, {6, 22}, {6, 26}, {6, 30}, {6, 34}, {6, 22, 38}, {6, 24, 42}, {6, 26, 46}, {6, 28, 50}
    };

    private final int version;
    private final int size;
    private final boolean[][] modules;
    private final boolean[][] function;
    private int mask;

    private QrCode(int version) {
        this.version = version;
        this.size = version * 4 + 17;
        this.modules = new boolean[size][size];
        this.function = new boolean[size][size];
    }

    /** UTF-8 文本的二维码；超出版本 10 容量时抛 {@link IllegalArgumentException}。 */
    public static QrCode encodeText(String text) {
        return encode(text.getBytes(StandardCharsets.UTF_8));
    }

    public static QrCode encode(byte[] payload) {
        int version = QrCodewords.smallestVersionFor(payload.length);
        QrCode qr = new QrCode(version);
        qr.drawFunctionPatterns();
        qr.drawCodewords(QrCodewords.interleave(QrCodewords.dataCodewords(payload, version), version));
        qr.applyBestMask();
        return qr;
    }

    public int version() {
        return version;
    }

    public int size() {
        return size;
    }

    /** 掩码编号（0–7）；调试与测试用。 */
    public int mask() {
        return mask;
    }

    public boolean dark(int x, int y) {
        return modules[y][x];
    }

    // --- 功能图案 ---

    private void drawFunctionPatterns() {
        for (int i = 0; i < size; i++) {
            setFunction(6, i, i % 2 == 0);
            setFunction(i, 6, i % 2 == 0);
        }
        drawFinder(3, 3);
        drawFinder(size - 4, 3);
        drawFinder(3, size - 4);
        int[] centres = ALIGNMENT[version];
        for (int i = 0; i < centres.length; i++) {
            for (int j = 0; j < centres.length; j++) {
                boolean atFinder = (i == 0 && j == 0)
                        || (i == 0 && j == centres.length - 1)
                        || (i == centres.length - 1 && j == 0);
                if (!atFinder) {
                    drawAlignment(centres[i], centres[j]);
                }
            }
        }
        // 先占位，掩码定下来后再写真正的格式信息。
        drawFormatInformation(0);
        drawVersionInformation();
    }

    /** 7×7 定位图案加一圈分隔符；中心坐标给定，越界的部分丢弃。 */
    private void drawFinder(int cx, int cy) {
        for (int dy = -4; dy <= 4; dy++) {
            for (int dx = -4; dx <= 4; dx++) {
                int x = cx + dx;
                int y = cy + dy;
                if (x < 0 || y < 0 || x >= size || y >= size) {
                    continue;
                }
                int ring = Math.max(Math.abs(dx), Math.abs(dy));
                setFunction(x, y, ring != 2 && ring != 4);
            }
        }
    }

    private void drawAlignment(int cx, int cy) {
        for (int dy = -2; dy <= 2; dy++) {
            for (int dx = -2; dx <= 2; dx++) {
                setFunction(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) != 1);
            }
        }
    }

    /** 格式信息：5 位（纠错等级 + 掩码号）经 BCH(15,5) 扩展，再异或 0x5412，两处副本各写一份。 */
    private void drawFormatInformation(int maskNumber) {
        int data = (EC_LEVEL_M << 3) | maskNumber;
        int bits = (data << 10) | bch(data << 10, FORMAT_GENERATOR, 10);
        bits ^= FORMAT_MASK;
        for (int i = 0; i <= 5; i++) {
            setFunction(8, i, bit(bits, i));
        }
        setFunction(8, 7, bit(bits, 6));
        setFunction(8, 8, bit(bits, 7));
        setFunction(7, 8, bit(bits, 8));
        for (int i = 9; i < 15; i++) {
            setFunction(14 - i, 8, bit(bits, i));
        }
        for (int i = 0; i < 8; i++) {
            setFunction(size - 1 - i, 8, bit(bits, i));
        }
        for (int i = 8; i < 15; i++) {
            setFunction(8, size - 15 + i, bit(bits, i));
        }
        setFunction(8, size - 8, true);
    }

    /** 版本 7 起额外写 18 位版本信息（6 位版本号 + BCH 校验）。 */
    private void drawVersionInformation() {
        if (version < 7) {
            return;
        }
        int bits = (version << 12) | bch(version << 12, VERSION_GENERATOR, 12);
        for (int i = 0; i < 18; i++) {
            boolean dark = bit(bits, i);
            int a = size - 11 + i % 3;
            int b = i / 3;
            setFunction(a, b, dark);
            setFunction(b, a, dark);
        }
    }

    // --- 数据排布与掩码 ---

    /** 从右下角起，两列一组上下蛇形填充，跳过第 6 列（定时图案）与所有功能模块。 */
    private void drawCodewords(byte[] codewords) {
        int index = 0;
        for (int right = size - 1; right >= 1; right -= 2) {
            if (right == 6) {
                right = 5;
            }
            boolean upward = ((right + 1) & 2) == 0;
            for (int vertical = 0; vertical < size; vertical++) {
                for (int column = 0; column < 2; column++) {
                    int x = right - column;
                    int y = upward ? size - 1 - vertical : vertical;
                    if (function[y][x] || index >= codewords.length * 8) {
                        continue;
                    }
                    modules[y][x] = ((codewords[index >>> 3] >>> (7 - (index & 7))) & 1) != 0;
                    index++;
                }
            }
        }
    }

    private void applyBestMask() {
        int best = 0;
        long bestPenalty = Long.MAX_VALUE;
        for (int candidate = 0; candidate < MASK_COUNT; candidate++) {
            applyMask(candidate);
            drawFormatInformation(candidate);
            long penalty = penalty();
            if (penalty < bestPenalty) {
                bestPenalty = penalty;
                best = candidate;
            }
            applyMask(candidate);
        }
        applyMask(best);
        drawFormatInformation(best);
        this.mask = best;
    }

    /** 异或掩码：再调用一次即还原。 */
    private void applyMask(int maskNumber) {
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) {
                if (!function[y][x] && maskBit(maskNumber, x, y)) {
                    modules[y][x] = !modules[y][x];
                }
            }
        }
    }

    private static boolean maskBit(int maskNumber, int x, int y) {
        return switch (maskNumber) {
            case 0 -> (x + y) % 2 == 0;
            case 1 -> y % 2 == 0;
            case 2 -> x % 3 == 0;
            case 3 -> (x + y) % 3 == 0;
            case 4 -> (y / 2 + x / 3) % 2 == 0;
            case 5 -> (x * y) % 2 + (x * y) % 3 == 0;
            case 6 -> ((x * y) % 2 + (x * y) % 3) % 2 == 0;
            case 7 -> ((x + y) % 2 + (x * y) % 3) % 2 == 0;
            default -> throw new IllegalArgumentException("mask " + maskNumber);
        };
    }

    /** 规范的四条惩罚规则，分数越低越好。 */
    private long penalty() {
        return QrPenalty.score(modules, size);
    }

    // --- 位工具 ---

    private void setFunction(int x, int y, boolean dark) {
        if (x < 0 || y < 0 || x >= size || y >= size) {
            return;
        }
        modules[y][x] = dark;
        function[y][x] = true;
    }

    private static boolean bit(int value, int index) {
        return ((value >>> index) & 1) != 0;
    }

    /** 二进制多项式取余，得到 BCH 校验位。 */
    private static int bch(int value, int generator, int checkBits) {
        int remainder = value;
        int generatorDegree = 31 - Integer.numberOfLeadingZeros(generator);
        while (remainder != 0 && (31 - Integer.numberOfLeadingZeros(remainder)) >= generatorDegree) {
            remainder ^= generator << ((31 - Integer.numberOfLeadingZeros(remainder)) - generatorDegree);
        }
        return remainder & ((1 << checkBits) - 1);
    }
}
