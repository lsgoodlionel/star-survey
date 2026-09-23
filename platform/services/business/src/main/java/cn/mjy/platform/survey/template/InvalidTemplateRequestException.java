package cn.mjy.platform.survey.template;

/** 请求本身不合法（400）：缺参数、驳回没给原因、scope 不认识。 */
public class InvalidTemplateRequestException extends RuntimeException {

    public InvalidTemplateRequestException(String message) {
        super(message);
    }
}
