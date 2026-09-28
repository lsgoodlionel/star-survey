package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * 纯单元：逐份答卷成文的 PDF（R06-05，ADR 0015 增补六）。排版结构与 DOCX 共用同一套
 * {@link ExportRecord} 视图，差别在写出器——PDF 自己排页、自己嵌字体。
 *
 * <p>读回一律走 {@link PdfReader}：按规范另写的一份最小解析器，从 {@code startxref} 顺着交叉引用表
 * 取对象、解 Flate、经 {@code ToUnicode} 把 2 字节编码还原成字符。<b>中文能不能被提取出来</b>
 * 靠它说话，而不是靠"文件里有这几个字节"。
 */
class PdfExportFormatTest {

    private static final List<String> CODES = List.of("version", "sid", "responseid", "generation", "state",
            "startedat", "completedat", "answersstatus", "QSINGLE", "QTABLE");
    private static final List<String> LABELS = List.of("版本", "引擎问卷号", "答卷号", "代次", "状态", "开始时间",
            "完成时间", "作答状态", "满意度", "随行物品 [结构化作答]");

    private static final ExportRecord FIRST = new ExportRecord(
            Arrays.asList("1", "900001", "7", "gen-a", "engine_completed", "2026-09-24T00:00:00Z", null,
                    "available", "=cmd|' /C calc'!A0", "[{\"item\":\"甲\"}]"),
            List.of(List.of("1", "900001", "7", "QSCAN", "U900", "1", "证件.png", "12.5", "png", "fu_1",
                    "a".repeat(64))),
            List.of(cell("7", "0", "item", "甲"), cell("7", "0", "qty", "2"),
                    cell("7", "1", "item", "乙"), cell("7", "1", "qty", "3")));

    private static final ExportRecord SECOND = new ExportRecord(
            Arrays.asList("1", "900001", "8", "gen-a", "engine_completed", null, null, "available", null, null),
            List.of(), List.of(cell("8", "", "", "")));

    /** 子集外的字符：日文假名、谚文、emoji，以及一个生僻字。 */
    private static final String OUTSIDE = "ぁ한😀龥";

    private static final ExportRecord OUT_OF_SUBSET = new ExportRecord(
            Arrays.asList("1", "900001", "9", "gen-a", "engine_completed", null, null, "available",
                    "很好" + OUTSIDE, null),
            List.of(), List.of());

    private static List<String> cell(String responseId, String rowIndex, String column, String value) {
        return List.of("1", "900001", responseId, "gen-a", "QTABLE", "rt1",
                rowIndex.isEmpty() ? "false" : "true", rowIndex, column, value);
    }

    private static ExportContent content(ExportRecord... records) {
        ExportSheet responses = new ExportSheet("responses", "答卷", 2, List.of(), sink -> {
            sink.accept(CODES);
            sink.accept(LABELS);
            for (ExportRecord record : records) {
                sink.accept(record.cells());
            }
        });
        return new ExportContent(List.of(responses), CODES, LABELS, sink -> {
            for (ExportRecord record : records) {
                sink.accept(record);
            }
        }, null);
    }

    private static byte[] write(ExportRecord... records) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ExportFormat.PDF.writer().write(content(records), out);
        return out.toByteArray();
    }

    private static PdfReader read(ExportRecord... records) throws IOException {
        return PdfReader.of(write(records));
    }

    /** 整份文档的文本，按出现顺序拼起来。 */
    private static String allText(PdfReader pdf) {
        return pdf.pages().stream().map(PdfReader.Page::text).reduce("", String::concat);
    }

    @Test
    void theFileIsAWellFormedPdfWhoseCrossReferenceTableReallyPointsAtItsObjects() throws IOException {
        PdfReader pdf = read(FIRST, SECOND);

        assertThat(pdf.version()).startsWith("%PDF-1.");
        assertThat(pdf.endsProperly()).as("以 %%EOF 收尾").isTrue();
        // pages() 顺着 /Root → /Pages → /Kids 走，每一步都用交叉引用表里的偏移取对象；
        // 偏移写错、/Count 与孩子数不符，这里就抛了。
        assertThat(pdf.pages()).isNotEmpty();
        assertThat(pdf.trailer()).contains("/Root");
    }

    @Test
    void everyResponseStartsOnItsOwnPage() throws IOException {
        List<PdfReader.Page> pages = read(FIRST, SECOND).pages();

        assertThat(pages).hasSize(2);
        assertThat(pages.get(0).text()).startsWith("答卷 7");
        assertThat(pages.get(1).text()).startsWith("答卷 8");
    }

    /**
     * 这是这一项的要害：中文必须能从 PDF 里**提取**出来，而不是看着像中文。
     * 提取路径与真阅读器一致（内容流的 {@code Tj} 串 → {@code ToUnicode} → 字符）。
     */
    @Test
    void chineseTextIsReallyExtractableNotJustPaintedOnThePage() throws IOException {
        String text = allText(read(FIRST, SECOND));

        assertThat(text).contains("答卷信息", "版本", "引擎问卷号", "答卷号", "代次", "状态", "作答状态")
                .contains("作答", "满意度", "随行物品 [结构化作答]（结构版本 rt1）")
                .contains("附件", "序号", "名称", "存储名", "证件.png");
        assertThat(text).doesNotContain("�").doesNotContain("〓");
    }

    /** 副表作答在文档格式里被还原成真表格（与 DOCX 同一条理由：一次只持有一份答卷）。 */
    @Test
    void theSideTableIsPivotedBackIntoARealTable() throws IOException {
        String text = allText(read(FIRST));

        assertThat(text).contains("行", "item", "qty", "甲", "乙", "2", "3");
    }

    /** 闸门没过的那一题写的是话，不是一张空表——空表会被读成「作答者没填」。 */
    @Test
    void theGateKeptQuestionSaysSoInsteadOfShowingAnEmptyTable() throws IOException {
        String text = read(SECOND).pages().getFirst().text();

        assertThat(text).contains("未通过校验");
    }

    @Test
    void theChineseFontIsEmbeddedAsASubsetAndNotLeftToTheReader() throws IOException {
        PdfReader pdf = read(FIRST, SECOND);
        PdfReader.EmbeddedFont font = pdf.embeddedFont();

        // /FontFile2 存在（PdfReader 取不到就抛），且里面真有字形轮廓。
        assertThat(font.tables()).containsKeys("glyf", "loca", "head", "hhea", "hmtx", "maxp");
        assertThat(font.tables().get("glyf")).as("glyf 不能是空的").isNotEmpty();
        assertThat(font.program().length).as("整份子集字体（约 2 MB）不该被原样塞进来")
                .isLessThan(1_000_000);
        // 子集字体按惯例带六个大写字母的前缀，说明这不是完整字体。
        assertThat(font.fontName()).matches("/[A-Z]{6}\\+MJYSansSC");
        assertThat(pdf.type0Font()).contains("/Subtype /Type0").contains("/Encoding /Identity-H");
        assertThat(pdf.descendantFont()).contains("/Subtype /CIDFontType2");
    }

    /** 嵌进来的是**用到的**字形，不是整份字体：同一份文档用的字越少，字体程序越小。 */
    @Test
    void onlyTheGlyphsActuallyUsedAreEmbedded() throws IOException {
        int few = read(SECOND).embeddedFont().tables().get("glyf").length;
        int more = read(FIRST, SECOND, OUT_OF_SUBSET).embeddedFont().tables().get("glyf").length;

        assertThat(few).isPositive();
        assertThat(more).isGreaterThan(few);
    }

    /**
     * 子集外的字符（假名、谚文、emoji、生僻字）画成替代符，但 {@code ToUnicode} 里仍然是它自己——
     * 复制与检索拿到的是原文。宁愿这样也不整份作业失败：作答内容是作答者给的。
     */
    @Test
    void charactersOutsideTheSubsetStayExtractableAsThemselves() throws IOException {
        PdfReader pdf = read(OUT_OF_SUBSET);

        assertThat(pdf.pages().getFirst().text()).contains("很好" + OUTSIDE);
        assertThat(Arrays.stream(pdf.cidToGid()).filter(gid -> gid > 0).count())
                .as("替代符也指向一个真字形，不是 0（notdef）").isPositive();
    }

    /** 看起来像公式的文本照旧被挡住：PDF 自己不算公式，但整页可以复制进 Excel。 */
    @Test
    void formulaLookingTextIsGuardedHereToo() throws IOException {
        assertThat(allText(read(FIRST))).contains("'=cmd|' /C calc'!A0");
    }

    /** 长文本要换行、要翻页，不能画到页外去。 */
    @Test
    void longAnswersWrapAndSpillOntoFurtherPages() throws IOException {
        ExportRecord verbose = new ExportRecord(
                Arrays.asList("1", "900001", "9", "gen-a", "engine_completed", null, null, "available",
                        "很长的回答。".repeat(600), null),
                List.of(), List.of());

        assertThat(read(verbose).pages()).hasSizeGreaterThan(1);
    }

    @Test
    void theSameInputProducesByteIdenticalFiles() throws IOException {
        assertThat(write(FIRST, SECOND)).isEqualTo(write(FIRST, SECOND));
    }

    /**
     * PDF 里最容易破坏逐字节相同的两样东西：创建时间与文档 ID。两样都不能随跑一次变一次，
     * 否则崩溃恢复后的文件与一次跑完的不同（{@link ResponseExportResumeTest}）。
     */
    /**
     * PDF 里最容易破坏逐字节相同的两样东西：创建时间与文档 ID。两样都不能变，否则崩溃恢复后的
     * 文件与一次跑完的不同。
     *
     * <p><b>为什么要把 ID 的字面值钉死，而不是「两次写出相等」就算数</b>：崩溃恢复真正发生在
     * <b>另一个进程</b>里，而 {@link ResponseExportResumeTest} 的两次跑法在<b>同一个 JVM</b> 内。
     * 于是「每个 JVM 随机一次」的文档 ID 在那条测试里照样两次相同——真上线了却每次重启都变。
     * 这个盲区是实测出来的：把 ID 改成 {@code static final = UUID.randomUUID()} 之后，
     * 逐字节相同那条断言依旧是绿的。钉死字面值才关得住它。
     */
    @Test
    void thereIsNoTimestampAndTheDocumentIdIsAConstantNotSomethingComputedAtRuntime() throws IOException {
        byte[] pdf = write(FIRST);

        assertThat(new String(pdf, java.nio.charset.StandardCharsets.ISO_8859_1))
                .doesNotContain("/CreationDate").doesNotContain("/ModDate");
        assertThat(PdfReader.value(PdfReader.of(pdf).trailer(), "/ID"))
                .as("文档 ID 必须是编译期常量：跨进程也得是同一个值")
                .isEqualTo("[<4d4a59504446303030303030303030303030303031>"
                        + " <4d4a59504446303030303030303030303030303031>]");
    }

    /** 30 万答卷的 PDF 没人打得开：与 DOCX 同一个处置，超出上限时作业失败。 */
    @Test
    void theNumberOfResponsesIsCapped() {
        assertThat(ExportFormat.PDF.writer().maxDataRows()).isEqualTo(PdfExportWriter.MAX_RESPONSES);
    }

    /** 留一份样例在 target/ 下，供第三方阅读器（PyMuPDF / Acrobat / Preview）读回核对。 */
    @Test
    void aSamplePdfIsLeftInTargetForExternalReaders() throws IOException {
        Path sample = Path.of("target", "export-sample.pdf");
        Files.createDirectories(sample.getParent());
        Files.write(sample, write(FIRST, SECOND, OUT_OF_SUBSET));

        assertThat(sample).exists();
    }
}
