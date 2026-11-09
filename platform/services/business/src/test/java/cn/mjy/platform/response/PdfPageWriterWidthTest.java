package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class PdfPageWriterWidthTest {
    @Test
    void exactlyMeasuredMixedLabelDoesNotWrapButNarrowerWidthStillWraps() {
        PdfFontUse font = new PdfFontUse(PdfFontProgram.get());
        PdfPageWriter writer = new PdfPageWriter(font, page -> {});
        String label = "重审定稿cbfa2f";
        double size = 8.5;
        double width = font.width(label, size);
        assertThat(writer.wrap(label, width, size)).containsExactly(label);
        assertThat(writer.wrap(label, width - 1.0, size)).hasSizeGreaterThan(1);
    }
}
