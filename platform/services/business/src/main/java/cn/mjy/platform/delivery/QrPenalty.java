package cn.mjy.platform.delivery;

/**
 * 掩码惩罚评分（ISO/IEC 18004 §8.8.2 的四条规则）：连续同色、2×2 同色块、类定位图案、黑白比例失衡。
 * 分数只决定选哪个掩码，不影响符号是否可解；选得不好只是更难扫。
 */
final class QrPenalty {

    private static final int N1 = 3;
    private static final int N2 = 3;
    private static final int N3 = 40;
    private static final int N4 = 10;
    private static final int HISTORY = 7;

    private QrPenalty() {
    }

    static long score(boolean[][] modules, int size) {
        long result = 0;
        result += lines(modules, size, true);
        result += lines(modules, size, false);
        result += blocks(modules, size);
        result += balance(modules, size);
        return result;
    }

    /** 规则 1（≥5 连同色）与规则 3（1:1:3:1:1 类定位图案），按行或按列各算一遍。 */
    private static long lines(boolean[][] modules, int size, boolean byRow) {
        long result = 0;
        for (int outer = 0; outer < size; outer++) {
            boolean runColor = false;
            int runLength = 0;
            int[] history = new int[HISTORY];
            for (int inner = 0; inner < size; inner++) {
                boolean dark = byRow ? modules[outer][inner] : modules[inner][outer];
                if (dark == runColor) {
                    runLength++;
                    if (runLength == 5) {
                        result += N1;
                    } else if (runLength > 5) {
                        result++;
                    }
                } else {
                    addHistory(runLength, history, size);
                    if (!runColor) {
                        result += (long) countFinderLike(history) * N3;
                    }
                    runColor = dark;
                    runLength = 1;
                }
            }
            result += (long) terminate(runColor, runLength, history, size) * N3;
        }
        return result;
    }

    /** 规则 2：每个 2×2 同色块加分。 */
    private static long blocks(boolean[][] modules, int size) {
        long result = 0;
        for (int y = 0; y < size - 1; y++) {
            for (int x = 0; x < size - 1; x++) {
                boolean color = modules[y][x];
                if (color == modules[y][x + 1] && color == modules[y + 1][x] && color == modules[y + 1][x + 1]) {
                    result += N2;
                }
            }
        }
        return result;
    }

    /** 规则 4：黑模块比例每偏离 50% 五个百分点加一档。 */
    private static long balance(boolean[][] modules, int size) {
        int dark = 0;
        for (boolean[] row : modules) {
            for (boolean module : row) {
                if (module) {
                    dark++;
                }
            }
        }
        int total = size * size;
        int steps = (Math.abs(dark * 20 - total * 10) + total - 1) / total - 1;
        return (long) steps * N4;
    }

    private static void addHistory(int runLength, int[] history, int size) {
        int length = runLength;
        if (history[0] == 0) {
            length += size;
        }
        System.arraycopy(history, 0, history, 1, history.length - 1);
        history[0] = length;
    }

    private static int terminate(boolean runColor, int runLength, int[] history, int size) {
        int length = runLength;
        if (runColor) {
            addHistory(length, history, size);
            length = 0;
        }
        addHistory(length + size, history, size);
        return countFinderLike(history);
    }

    private static int countFinderLike(int[] history) {
        int n = history[1];
        boolean core = n > 0 && history[2] == n && history[3] == n * 3 && history[4] == n && history[5] == n;
        return (core && history[0] >= n * 4 && history[6] >= n ? 1 : 0)
                + (core && history[6] >= n * 4 && history[0] >= n ? 1 : 0);
    }
}
