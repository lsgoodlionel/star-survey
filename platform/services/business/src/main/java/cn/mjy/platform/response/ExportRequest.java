package cn.mjy.platform.response;

import java.util.List;
import java.util.UUID;

/**
 * 创建导出的请求体（蓝图：filterSnapshot、format、templateVersion）。
 *
 * @param surveyId        只在 {@code POST /v1/exports} 上使用；问卷路径上的写法以路径为准，体里给了也必须一致
 * @param format          {@code csv} / {@code xlsx}
 * @param filter          筛选；为空表示全部状态
 * @param templateVersion 本切片只接受空或 {@code default}，给 Word/PDF 模板等后续格式预留
 */
public record ExportRequest(UUID surveyId, String format, ExportFilter filter, String templateVersion) {

    /** @param states 要导出的答卷状态；为空表示全部（in_progress、engine_completed、deleted） */
    public record ExportFilter(List<String> states) {

        public ExportFilter {
            states = states == null ? null : List.copyOf(states);
        }
    }
}
