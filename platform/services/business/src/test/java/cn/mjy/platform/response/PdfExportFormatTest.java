package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.text.Normalizer;
import java.util.Arrays;
import java.util.List;
import org.apache.pdfbox.Loader;
import org.apache.pdfbox.text.PDFTextStripper;
import org.junit.jupiter.api.Test;

/** 纯单元：逐份答卷 PDF 必须嵌入中文字体，并能由标准 PDF 读取器还原中文文本。 */
class PdfExportFormatTest {

    private static ExportContent content() {
        List<String> codes = List.of("version", "sid", "responseid", "generation", "state", "startedat",
                "completedat", "answersstatus", "QTEXT");
        List<String> labels = List.of("版本", "引擎问卷号", "答卷号", "代次", "状态", "开始时间", "完成时间",
                "作答状态", "中文意见");
        ExportRecord record = new ExportRecord(
                Arrays.asList("1", "900001", "7", "gen-a", "engine_completed", "2026-10-30T00:00:00Z",
                        "2026-10-30T00:01:00Z", "available", "非常满意"),
                List.of(), List.of());
        ExportSheet responses = new ExportSheet("responses", "答卷", 2, List.of(), sink -> {
            sink.accept(codes);
            sink.accept(labels);
            sink.accept(record.cells());
        });
        return new ExportContent(List.of(responses), codes, labels, sink -> sink.accept(record), null);
    }

    private static byte[] write() throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ExportFormat.PDF.writer().write(content(), out);
        return out.toByteArray();
    }

    @Test
    void pdfIsRealNonEmptyAndChineseTextRoundTrips() throws IOException {
        byte[] pdf = write();
        assertThat(pdf).startsWith('%', 'P', 'D', 'F', '-').hasSizeGreaterThan(1_000);

        try (var document = Loader.loadPDF(pdf)) {
            String text = Normalizer.normalize(new PDFTextStripper().getText(document), Normalizer.Form.NFKC);
            assertThat(document.getNumberOfPages()).isEqualTo(1);
            assertThat(text).contains("答卷 7", "答卷信息", "作答", "中文意见", "非常满意");
            assertThat(document.getPage(0).getResources().getFontNames()).isNotEmpty();
        }
    }

    @Test
    void sameInputProducesByteIdenticalPdf() throws IOException {
        assertThat(write()).isEqualTo(write());
    }

    @Test
    void formatMetadataIsPublicAndCaseInsensitive() {
        assertThat(ExportFormat.fromCode("PDF")).contains(ExportFormat.PDF);
        assertThat(ExportFormat.PDF.contentType()).isEqualTo("application/pdf");
        assertThat(ExportFormat.PDF.extension()).isEqualTo("pdf");
        assertThat(ExportFormat.PDF.writer().maxDataRows()).isEqualTo(PdfExportWriter.MAX_RESPONSES);
    }
}
