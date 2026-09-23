package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** 短链标识：不可枚举、不可预测、不含易混字符。 */
class ShortCodesTest {

    private static final int SAMPLE = 20_000;

    @Test
    void codesAreLongEnoughThatGuessingIsHopeless() {
        // 31 个字符 × 13 位 ≈ 64 位随机；按每秒一万次猜测算，撞到一个有效短链平均要上亿年。
        assertThat(ShortCodes.ALPHABET).hasSize(31);
        assertThat(ShortCodes.LENGTH).isEqualTo(13);
        assertThat(ShortCodes.entropyBits()).isGreaterThanOrEqualTo(64);
    }

    @Test
    void codesAreUnpredictableAndDoNotRepeat() {
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < SAMPLE; i++) {
            String code = ShortCodes.random();
            assertThat(code).matches(ShortCodes.CODE_REGEX);
            assertThat(seen.add(code)).describedAs("duplicate short code %s", code).isTrue();
        }
    }

    /** 相邻两次生成不得只差一位：自增或时间派生的标识可以被顺着数出来。 */
    @Test
    void consecutiveCodesDifferInManyPositions() {
        int adjacentPairsDifferingInOnePosition = 0;
        String previous = ShortCodes.random();
        for (int i = 0; i < 1_000; i++) {
            String current = ShortCodes.random();
            if (positionsDiffering(previous, current) <= 1) {
                adjacentPairsDifferingInOnePosition++;
            }
            previous = current;
        }
        assertThat(adjacentPairsDifferingInOnePosition).isZero();
    }

    @Test
    void theAlphabetExcludesEasilyConfusedCharacters() {
        assertThat(ShortCodes.ALPHABET).doesNotContain("0", "O", "1", "l", "I", "i");
        assertThat(ShortCodes.ALPHABET).isLowerCase();
    }

    @Test
    void rejectsCodesOfTheWrongShapeWithoutTouchingStorage() {
        assertThat(ShortCodes.isWellFormed("abcdefghjkmnp")).isTrue();
        assertThat(ShortCodes.isWellFormed("ABCDEFGHJKMNP")).isFalse();
        assertThat(ShortCodes.isWellFormed("abcdefghjkmn")).isFalse();
        assertThat(ShortCodes.isWellFormed("abcdefghjkmn0")).isFalse();
        assertThat(ShortCodes.isWellFormed("abcdefgh jkmn")).isFalse();
        assertThat(ShortCodes.isWellFormed(null)).isFalse();
    }

    private static int positionsDiffering(String a, String b) {
        int differing = 0;
        for (int i = 0; i < a.length(); i++) {
            if (a.charAt(i) != b.charAt(i)) {
                differing++;
            }
        }
        return differing;
    }
}
