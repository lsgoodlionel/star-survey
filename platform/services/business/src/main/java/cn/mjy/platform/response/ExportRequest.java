package cn.mjy.platform.response;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import tools.jackson.databind.JsonNode;

/**
 * 创建导出的请求体（蓝图：filterSnapshot、format、templateVersion）。
 *
 * @param surveyId        只在 {@code POST /v1/exports} 上使用；问卷路径上的写法以路径为准，体里给了也必须一致
 * @param format          {@code csv} / {@code xlsx} / {@code sav}
 * @param filter          筛选；为空表示全部状态和全部已发布版本
 * @param templateVersion 目前只接受空或 {@code default}，给 Word/PDF 模板等后续格式预留
 */
public record ExportRequest(UUID surveyId, String format, ExportFilter filter, String templateVersion) {

    /**
     * @param states   要导出的答卷状态；为空表示全部（in_progress、engine_completed、deleted）
     * @param versions 要导出的已发布版本号；为空表示全部
     */
    public record ExportFilter(List<String> states, List<PublishedVersion> versions) {

        public ExportFilter {
            states = states == null ? null : List.copyOf(states);
            // 保留 null 元素给 ExportSpec 统一转换成 400，而不是在 JSON 绑定期抛 NPE。
            versions = versions == null ? null
                    : Collections.unmodifiableList(new ArrayList<>(versions));
        }
    }

    /** JSON 边界上的已发布版本号：只接受正数 integer token，不接受字符串或浮点数 coercion。 */
    public record PublishedVersion(int value) {

        @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
        static PublishedVersion fromJson(JsonNode node) {
            if (node == null || !node.isIntegralNumber() || !node.canConvertToInt() || node.intValue() < 1) {
                throw new InvalidResponseQueryException(
                        "filter.versions must contain positive integer tokens");
            }
            return new PublishedVersion(node.intValue());
        }

        @JsonValue
        int toJson() {
            return value;
        }
    }
}
