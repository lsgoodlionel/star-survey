package cn.mjy.platform.survey;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import tools.jackson.databind.JsonNode;

/**
 * 不可变的已发布版本：当时发给网关的定义快照、绑定（实例、sid、指纹、编译器版本）
 * 以及"题目 UUID → 题目代码 → 答卷列名"映射。
 */
public record PublishedVersionView(
        UUID surveyId,
        int version,
        UUID requestId,
        int draftVersion,
        String engineInstanceId,
        int engineSid,
        String compilerVersion,
        String fingerprintVersion,
        String fingerprint,
        String language,
        String enginePublishedAt,
        String publishedBy,
        OffsetDateTime publishedAt,
        List<QuestionFieldView> fields,
        JsonNode definition) {

    public PublishedVersionView {
        fields = List.copyOf(fields);
    }

    /** 映射里的一列：一道题可能占多列（"其他"、子题、双尺度）。 */
    public record QuestionFieldView(
            UUID questionUuid, String code, String type, String fieldname, String aid, int scale) {
    }
}
