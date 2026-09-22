package cn.mjy.platform.response;

import java.util.List;
import java.util.UUID;

/**
 * 字段字典：每个已发布版本一段，逐列说明引擎答卷列的含义（ADR 0013 决定 4）。
 * 全部来自发布时冻结的定义快照与绑定映射，不访问引擎。
 */
public record FieldDictionary(UUID surveyId, List<VersionFields> versions) {

    public FieldDictionary {
        versions = List.copyOf(versions);
    }

    public record VersionFields(int version, String engineInstanceId, long engineSid, String language,
            List<FieldEntry> fields) {

        public VersionFields {
            fields = List.copyOf(fields);
        }
    }

    /**
     * 一列。
     *
     * @param aid       子题代码 / {@code other} / {@code …comment}；单列题为空串
     * @param scale     双尺度题的尺度号
     * @param label     题干（子题、"其他"、评论列附在方括号或括号里）
     * @param sensitive 该题在定义里标了 {@code "sensitive": true}
     * @param options   该列可取的选项代码与文本（同尺度）；自由文本、"其他"与评论列为空
     */
    public record FieldEntry(String fieldname, UUID questionUuid, String code, String type, String aid, int scale,
            String label, boolean sensitive, List<OptionLabel> options) {

        public FieldEntry {
            options = List.copyOf(options);
        }
    }

    public record OptionLabel(String code, String text) {
    }
}
