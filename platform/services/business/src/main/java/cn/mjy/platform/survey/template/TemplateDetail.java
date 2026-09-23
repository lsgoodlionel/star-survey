package cn.mjy.platform.survey.template;

import java.util.List;

/** 模板详情：当前状态 ＋ 版本清单 ＋ 动作历史。 */
public record TemplateDetail(TemplateView template, List<TemplateVersionView> versions,
        List<TemplateEventView> history) {

    public TemplateDetail {
        versions = List.copyOf(versions);
        history = List.copyOf(history);
    }
}
