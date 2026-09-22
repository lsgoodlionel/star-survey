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
     * @param latestGeneration 创建时该 sid 最近出现的代次；为 {@code null} 表示当时还没有任何答卷
     */
    record PlannedSource(int version, String engineInstanceId, long engineSid, String latestGeneration,
            List<FieldEntry> fields) {

        PlannedSource {
            fields = List.copyOf(fields);
        }

        List<String> fieldnames() {
            return fields.stream().map(FieldEntry::fieldname).distinct().toList();
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
