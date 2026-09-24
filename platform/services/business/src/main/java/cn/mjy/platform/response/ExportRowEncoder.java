package cn.mjy.platform.response;

import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.AnswerBatch.ExtensionAnswer;
import cn.mjy.platform.response.AttachmentManifest.Attachment;
import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.FieldDictionary.FieldEntry;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 把一批快照行＋该批作答编码成表格行（已遮蔽）与附件清单行。纯函数：同样的输入得到同样的行，
 * 崩溃恢复重做一批时分片内容逐字节相同。作答状态的判定与明细列表一致（ADR 0013 决定 3）。
 */
final class ExportRowEncoder {

    /**
     * 一批的编码结果。
     *
     * @param extensions 扩展副表作答，一个单元格一行（ADR 0015 增补二）；行数取决于各份答卷填了几行，
     *                   与答卷数不成比例
     */
    record Encoded(List<List<String>> rows, List<List<String>> attachments, List<List<String>> extensions) {

        Encoded {
            rows = List.copyOf(rows);
            attachments = List.copyOf(attachments);
            extensions = List.copyOf(extensions);
        }
    }

    private final ExportLayout layout;
    private final ResponseFieldPolicy policy;
    /** 版本号 → 该版本里标了敏感的题目代码；每批算一次，不逐行重算。 */
    private final Map<Integer, Set<String>> sensitiveQuestions;

    ExportRowEncoder(ExportLayout layout, ResponseFieldPolicy policy) {
        this.layout = layout;
        this.policy = policy;
        this.sensitiveQuestions = layout.plan().sources().stream()
                .collect(Collectors.toUnmodifiableMap(PlannedSource::version,
                        PlannedSource::sensitiveQuestionCodes));
    }

    /** 该行要不要去引擎取作答：未删除且属于创建时的最近代次。 */
    static boolean needsAnswers(ExportItem item, PlannedSource source) {
        return !ResponseState.DELETED.dbValue().equals(item.state())
                && item.generation().equals(source.latestGeneration());
    }

    /**
     * @param answers 版本号 → 该批从引擎读到的作答
     */
    Encoded encode(List<ExportItem> items, Map<Integer, AnswerBatch> answers) {
        List<List<String>> rows = new ArrayList<>(items.size());
        List<List<String>> attachments = new ArrayList<>();
        List<List<String>> extensions = new ArrayList<>();
        for (ExportItem item : items) {
            PlannedSource source = layout.plan().source(item.version());
            AnswerBatch batch = answers.getOrDefault(item.version(), AnswerBatch.empty());
            Map<String, String> raw = needsAnswers(item, source) ? batch.answers().get(item.responseId()) : null;
            Map<String, String> values = raw == null ? null : policy.apply(raw);
            rows.add(row(item, source, status(item, source, raw), values));
            if (values != null) {
                attachments.addAll(attachments(item, source, values));
            }
            if (needsAnswers(item, source)) {
                extensions.addAll(extensions(item, source, batch));
            }
        }
        return new Encoded(rows, attachments, extensions);
    }

    /**
     * 一道副表题的全部单元格，每格一行，顺序是 (题目, 行序, 列序)——列序即插件写入时的列字典顺序。
     *
     * <p>一行单元格都没有的题目仍然留一行（行序与列为空）：闸门没过时副表本来就是空的，
     * 引擎答卷列里却还留着不可信的原文，导出必须让人一眼看出「这条不可信」而不是「作答者没填」
     * （契约 question-extension-tables-v1 第三节第 1 条）。
     */
    private List<List<String>> extensions(ExportItem item, PlannedSource source, AnswerBatch batch) {
        Set<String> sensitive = sensitiveQuestions.getOrDefault(source.version(), Set.of());
        List<List<String>> rows = new ArrayList<>();
        for (String question : source.extensionQuestions()) {
            ExtensionAnswer answer = batch.extension(item.responseId(), question);
            if (answer == null) {
                continue;
            }
            boolean masked = sensitive.contains(question);
            if (answer.rows().isEmpty()) {
                rows.add(extensionRow(item, question, answer, "", "", ""));
                continue;
            }
            for (int index = 0; index < answer.rows().size(); index++) {
                String rowIndex = Integer.toString(index);
                answer.rows().get(index).forEach((column, value) ->
                        rows.add(extensionRow(item, question, answer, rowIndex, column,
                                Objects.toString(policy.cell(value, masked), ""))));
            }
        }
        return rows;
    }

    private static List<String> extensionRow(ExportItem item, String question, ExtensionAnswer answer,
            String rowIndex, String column, String value) {
        return List.of(Integer.toString(item.version()), Long.toString(item.engineSid()),
                Long.toString(item.responseId()), item.generation(), question, answer.structureVersion(),
                Boolean.toString(answer.valid()), rowIndex, column, value);
    }

    private static AnswersStatus status(ExportItem item, PlannedSource source, Map<String, String> raw) {
        if (ResponseState.DELETED.dbValue().equals(item.state())) {
            return AnswersStatus.DELETED;
        }
        if (!item.generation().equals(source.latestGeneration())) {
            return AnswersStatus.ARCHIVED;
        }
        return raw == null ? AnswersStatus.MISSING : AnswersStatus.AVAILABLE;
    }

    private List<String> row(ExportItem item, PlannedSource source, AnswersStatus status, Map<String, String> values) {
        String[] cells = new String[layout.width()];
        cells[0] = Integer.toString(item.version());
        cells[1] = Long.toString(item.engineSid());
        cells[2] = Long.toString(item.responseId());
        cells[3] = item.generation();
        cells[4] = item.state();
        cells[5] = time(item.firstEventAt());
        cells[6] = time(item.completedAt());
        cells[7] = status.wire();
        if (values != null) {
            int offset = ExportLayout.FIXED_CODES.size();
            layout.positions(source.version()).forEach((field, index) -> cells[offset + index] = values.get(field));
        }
        return Arrays.asList(cells);
    }

    private List<List<String>> attachments(ExportItem item, PlannedSource source, Map<String, String> values) {
        List<List<String>> rows = new ArrayList<>();
        Map<String, Integer> positions = layout.positions(source.version());
        for (FieldEntry field : source.fields()) {
            if (!AttachmentManifest.UPLOAD_TYPE.equals(field.type()) || !field.aid().isEmpty()) {
                continue;
            }
            List<Attachment> files = AttachmentManifest.parse(values.get(field.fieldname()));
            String column = layout.columns().get(positions.get(field.fieldname())).code();
            for (int i = 0; i < files.size(); i++) {
                Attachment file = files.get(i);
                rows.add(List.of(Integer.toString(item.version()), Long.toString(item.engineSid()),
                        Long.toString(item.responseId()), column, field.fieldname(), Integer.toString(i + 1),
                        file.name(), file.size(), file.ext(), file.storedName(), file.sha256()));
            }
        }
        return rows;
    }

    private static String time(Instant instant) {
        return Objects.toString(instant, null);
    }
}
