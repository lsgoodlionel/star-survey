package cn.mjy.platform.survey;

/** 请求本身不合法（如把问卷挂在另一份问卷下面），对应 400。 */
public class InvalidSurveyRequestException extends RuntimeException {

    public InvalidSurveyRequestException(String message) {
        super(message);
    }
}
