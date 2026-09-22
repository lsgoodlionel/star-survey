package cn.mjy.platform.survey;

/** 一次发布调用的结果：问卷最新状态；成功时附带新产生的已发布版本，否则 version 为空。 */
public record PublishOutcome(SurveyView survey, PublishedVersionView version) {
}
