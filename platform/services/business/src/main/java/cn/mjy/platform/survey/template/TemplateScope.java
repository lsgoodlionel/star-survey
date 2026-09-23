package cn.mjy.platform.survey.template;

import com.fasterxml.jackson.annotation.JsonValue;

/** 模板的归属：租户私有，或平台（运营）发布给所有租户。列表接口的 {@code scope} 参数还接受 {@code all}。 */
public enum TemplateScope {
    TENANT,
    PLATFORM,
    ALL;

    @JsonValue
    public String code() {
        return name().toLowerCase();
    }

    public static TemplateScope fromCode(String code) {
        if (code == null || code.isBlank()) {
            return ALL;
        }
        try {
            return valueOf(code.trim().toUpperCase());
        } catch (IllegalArgumentException e) {
            throw new InvalidTemplateRequestException("unknown scope: " + code);
        }
    }
}
