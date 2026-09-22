package cn.mjy.platform.response;

import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.AttachmentManifest.Attachment;
import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.FieldDictionary.FieldEntry;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 把一批快照行＋该批作答编码成表格行（已遮蔽）与附件清单行。纯函数：同样的输入得到同样的行，
 * 崩溃恢复重做一批时分片内容逐字节相同。作答状态的判定与明细列表一致（ADR 0013 决定 3）。
 */
final class ExportRowEncoder {

    /** 一批的编码结果。 */
    record Encoded(List<List<String>> rows, List<List<String>> attachments) {

        Encoded {
            rows = List.copyOf(rows);
            attachments = List.copyOf(attachments);
        }
    }

    private final ExportLayout layout;
    private final ResponseFieldPolicy policy;

    ExportRowEncoder(ExportLayout layout, ResponseFieldPolicy policy) {
        this.layout = layout;
        this.policy = policy;
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
        for (ExportItem item : items) {
            PlannedSource source = layout.plan().source(item.version());
            Map<String, String> raw = needsAnswers(item, source)
                    ? answers.getOrDefault(item.version(), AnswerBatch.empty()).answers().get(item.responseId())
                    : null;
            Map<String, String> values = raw == null ? null : policy.apply(raw);
            rows.add(row(item, source, status(item, source, raw), values));
            if (values != null) {
                attachments.addAll(attachments(item, source, values));
            }
        }
        return new Encoded(rows, attachments);
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
