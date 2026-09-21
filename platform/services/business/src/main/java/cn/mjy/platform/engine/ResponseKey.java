package cn.mjy.platform.engine;

/**
 * 答卷自然键（租户之外的部分）：(引擎实例, 问卷, 代次, 答卷号)。
 * 代次必不可少：停用再激活会重建答卷表、答卷号从 1 重新计数（ADR 0003）。
 */
public record ResponseKey(String engineInstanceId, long surveyId, String generation, long responseId) {
}
