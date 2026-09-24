package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.security.SignedParameters;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * 二维码编码器（ISO/IEC 18004，字节模式、纠错等级 M、版本 1–10）。
 *
 * <p>验证方式有三层，互相独立：
 * <ol>
 *   <li><b>已知编码</b>：单字节 "A" 在版本 1 下的数据码字可以按规范逐位推导，写成固定值比对；</li>
 *   <li><b>独立解码</b>：{@link QrMatrixDecoder}（测试自带，不复用被测代码的排布与掩码逻辑）
 *       把矩阵读回码字，再解出模式、长度与原文；</li>
 *   <li><b>纠错校验</b>：解码器用自己的 GF(256) 实现算每个分块的伴随式，全为零才算通过——
 *       里德-所罗门码字算错时伴随式一定非零。</li>
 * </ol>
 */
class QrCodeTest {

    /**
     * "A" 在版本 1 字节模式下的数据码字，按 ISO/IEC 18004 §8.4 逐位推导：
     * 模式 0100 + 字符数 00000001（版本 1–9 为 8 位）+ 0x41 的 01000001 + 终止符 0000
     * = 0x40 0x14 0x10，其后按 0xEC/0x11 交替填满 16 个数据码字。
     */
    @Test
    void encodesTheHandDerivedDataCodewordsOfASingleByte() {
        byte[] codewords = QrCodewords.dataCodewords("A".getBytes(StandardCharsets.UTF_8), 1);
        assertThat(HexFormat.of().formatHex(codewords))
                .isEqualTo("401410ec11ec11ec11ec11ec11ec11ec");
    }

    @Test
    void aVersionOneSymbolIsTwentyOneModulesWideWithFinderPatterns() {
        QrCode qr = QrCode.encode("A".getBytes(StandardCharsets.UTF_8));
        assertThat(qr.version()).isEqualTo(1);
        assertThat(qr.size()).isEqualTo(21);
        for (int[] origin : new int[][] {{0, 0}, {14, 0}, {0, 14}}) {
            assertFinderPatternAt(qr, origin[0], origin[1]);
        }
        // 定时图案：第 6 行/列在功能区之间黑白交替，且 (8, 4×1+9) 恒为黑（格式信息旁的固定黑点）。
        for (int i = 8; i < qr.size() - 8; i++) {
            assertThat(qr.dark(i, 6)).isEqualTo(i % 2 == 0);
            assertThat(qr.dark(6, i)).isEqualTo(i % 2 == 0);
        }
        assertThat(qr.dark(8, qr.size() - 8)).isTrue();
    }

    /** 格式信息是 BCH(15,5) 码字异或 0x5412；两份副本必须一致，且能被生成多项式 0x537 整除。 */
    @Test
    void theTwoCopiesOfTheFormatInformationAgreeAndAreValidBchCodewords() {
        QrCode qr = QrCode.encode(longPayload(40));
        int primary = QrMatrixDecoder.primaryFormatBits(qr);
        int secondary = QrMatrixDecoder.secondaryFormatBits(qr);
        assertThat(primary).isEqualTo(secondary);
        assertThat(QrMatrixDecoder.bchRemainder(primary ^ 0x5412, 0x537, 10)).isZero();
    }

    @Test
    void decodesBackToTheOriginalPayloadAcrossVersionsAndBlockLayouts() {
        for (int length : new int[] {1, 10, 16, 30, 50, 80, 120, 180, 213}) {
            byte[] payload = longPayload(length);
            QrCode qr = QrCode.encode(payload);
            assertThat(QrMatrixDecoder.decode(qr))
                    .describedAs("payload of %d bytes in version %d", length, qr.version())
                    .isEqualTo(payload);
        }
    }

    @Test
    void decodesATypicalRespondentUrlWithSignedParameters() {
        byte[] payload = ("https://survey.example/index.php/912345?src=wechat&exp=1790000000"
                + "&sig=Yk9wT3hQb1JzVHVWd1h5WjAxMjM0NTY3ODlhYmNkZQ").getBytes(StandardCharsets.UTF_8);
        QrCode qr = QrCode.encode(payload);
        assertThat(QrMatrixDecoder.decode(qr)).isEqualTo(payload);
    }

    /** 纠错码字算错时伴随式非零；解码器对每个分块独立验算。 */
    @Test
    void everyBlockCarriesConsistentReedSolomonCheckSymbols() {
        for (int version = 1; version <= 10; version++) {
            QrCode qr = QrCode.encode(longPayload(QrCodewords.maxPayloadBytes(version)));
            assertThat(qr.version()).isEqualTo(version);
            List<int[]> syndromes = QrMatrixDecoder.blockSyndromes(qr);
            assertThat(syndromes).isNotEmpty();
            for (int[] syndrome : syndromes) {
                assertThat(syndrome).describedAs("version %d syndromes", version).containsOnly(0);
            }
        }
    }

    @Test
    void refusesPayloadsBeyondTheLargestSupportedVersion() {
        assertThatThrownBy(() -> QrCode.encode(longPayload(QrCodewords.maxPayloadBytes(10) + 1)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void theSvgAndPngRenderingsShowTheSameModules() {
        QrCode qr = QrCode.encode("https://survey.example/s/abc".getBytes(StandardCharsets.UTF_8));
        String svg = QrCodeRenderer.svg(qr, 4, 4);
        assertThat(svg).startsWith("<?xml").contains("viewBox=\"0 0 " + (qr.size() + 8) + " " + (qr.size() + 8) + "\"");
        assertThat(svg).doesNotContain("<script");

        byte[] png = QrCodeRenderer.png(qr, 4, 4);
        assertThat(HexFormat.of().formatHex(png, 0, 8)).isEqualTo("89504e470d0a1a0a");
        assertThat(QrMatrixDecoder.readPngModules(png, 4, 4, qr.size())).isEqualTo(QrMatrixDecoder.modules(qr));
    }

    private static void assertFinderPatternAt(QrCode qr, int x0, int y0) {
        for (int dy = 0; dy < 7; dy++) {
            for (int dx = 0; dx < 7; dx++) {
                int ring = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
                assertThat(qr.dark(x0 + dx, y0 + dy))
                        .describedAs("finder module (%d,%d)", x0 + dx, y0 + dy)
                        .isEqualTo(ring != 2);
            }
        }
    }

    private static byte[] longPayload(int length) {
        byte[] payload = new byte[length];
        for (int i = 0; i < length; i++) {
            payload[i] = (byte) ('0' + (i % 10));
        }
        return payload;
    }
}
