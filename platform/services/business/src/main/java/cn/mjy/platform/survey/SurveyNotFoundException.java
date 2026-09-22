package cn.mjy.platform.survey;

/** 问卷（或其某个已发布版本）不存在，或属于别的租户——对调用方两者不可区分，统一 404。 */
public class SurveyNotFoundException extends RuntimeException {

    public SurveyNotFoundException(String message) {
        super(message);
    }
}
