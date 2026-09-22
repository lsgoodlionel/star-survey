package cn.mjy.platform.survey;

/** 发布网关未配置（503）。在改动任何状态之前抛出，问卷保持原状。 */
public class PublishUnavailableException extends RuntimeException {

    public PublishUnavailableException(String message) {
        super(message);
    }
}
