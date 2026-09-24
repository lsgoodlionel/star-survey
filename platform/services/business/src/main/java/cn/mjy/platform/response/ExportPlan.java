package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.FieldEntry;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 导出计划：作业创建时冻结的来源（版本、(实例, sid)、当时的最近代次、字段字典），存进作业行的 plan 列。
 * 导出期间重新发布出现的新版本不进入本次导出，表头在重跑、恢复时逐字节相同（ADR 0015 决定 1）。
 */
record ExportPlan(List<PlannedSource> sources) {

    ExportPlan {
        sources = List.copyOf(sources);
    }

    /**
     * @param latestGeneration   创建时该 sid 最近出现的代次；为 {@code null} 表示当时还没有任何答卷
     * @param extensionQuestions 该版本里有副表的题目代码，顺序即定义顺序；切片 06.4 之前建的作业没有这一段，
     *                           反序列化成空表（旧作业照旧不带副表作答，不会因为升级而改变文件内容）
     */
    record PlannedSource(int version, String engineInstanceId, long engineSid, String latestGeneration,
            List<FieldEntry> fields, List<String> extensionQuestions) {

        PlannedSource {
            fields = List.copyOf(fields);
            extensionQuestions = extensionQuestions == null ? List.of() : List.copyOf(extensionQuestions);
        }

        List<String> fieldnames() {
            return fields.stream().map(FieldEntry::fieldname).distinct().toList();
        }

        /** 标了敏感的副表题：它们的单元格与那一列 JSON 信封同进同退，不能一处遮蔽另一处明文。 */
        Set<String> sensitiveQuestionCodes() {
            return fields.stream().filter(FieldEntry::sensitive).map(FieldEntry::code)
                    .collect(Collectors.toUnmodifiableSet());
        }
    }

    PlannedSource source(int version) {
        return sources.stream().filter(s -> s.version() == version).findFirst()
                .orElseThrow(() -> new IllegalStateException("export plan has no version " + version));
    }

    Set<String> sensitiveFieldnames() {
        return sources.stream().flatMap(s -> s.fields().stream()).filter(FieldEntry::sensitive)
                .map(FieldEntry::fieldname).collect(Collectors.toUnmodifiableSet());
    }
}
