package cn.mjy.platform.survey.template;

/**
 * 模板不存在。别的租户的模板、尚未上架的运营模板都走这里：调用方无法据此区分「没有」与「不属于你」。
 */
public class TemplateNotFoundException extends RuntimeException {

    public TemplateNotFoundException(String message) {
        super(message);
    }
}
