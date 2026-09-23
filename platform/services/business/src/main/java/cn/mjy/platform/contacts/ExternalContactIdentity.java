package cn.mjy.platform.contacts;

import java.util.Objects;
import java.util.regex.Pattern;

/**
 * 外部身份三件套。只有 {@code (provider, appId, externalId)} 整体才是一个身份：
 * 同一串 openid 换了 appId 就是另一个人（ADR 0017 决定 1），与 identity_binding 的键口径一致。
 */
public record ExternalContactIdentity(String provider, String appId, String externalId) {

    static final Pattern PROVIDER = Pattern.compile("[a-z][a-z0-9_]{1,31}");
    static final int MAX_APP_ID = 128;
    static final int MAX_EXTERNAL_ID = 256;

    public ExternalContactIdentity {
        Objects.requireNonNull(provider, "provider");
        Objects.requireNonNull(appId, "appId");
        Objects.requireNonNull(externalId, "externalId");
    }

    /** 去重值：三段拼接，任何一段变了就是另一个身份。 */
    String dedupeValue() {
        return provider + "|" + appId + "|" + externalId;
    }

    boolean isWellFormed() {
        return PROVIDER.matcher(provider).matches()
                && !appId.isBlank() && appId.length() <= MAX_APP_ID
                && !externalId.isBlank() && externalId.length() <= MAX_EXTERNAL_ID;
    }
}
