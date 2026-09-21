package cn.mjy.platform.onboarding;

import org.springframework.http.HttpStatus;

/** 开通被拒的原因；{@code code} 直接作为接口错误码返回。 */
public class OnboardingException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public OnboardingException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public HttpStatus status() {
        return status;
    }

    public String code() {
        return code;
    }
}
