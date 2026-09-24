package cn.mjy.platform.survey;

/**
 * 导入过程中的一个问题。{@code line} 是原文里的 1 起行号（整题级的问题指向题干那一行），
 * {@code code} 是稳定的机器可读错误码，{@code message} 是给作者看的中文说明。
 *
 * <p>问题从不"静默丢弃"：认不出来的行进 {@link SurveyImportPreview#problems()}，
 * 题目自身的问题进 {@link SurveyImportPreview.PreviewQuestion#problems()}，两者都原样回给调用方。
 */
public record ImportProblem(int line, String code, String message) {
}
