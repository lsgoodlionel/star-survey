package cn.mjy.platform.delivery;

import jakarta.mail.internet.AddressException;
import jakarta.mail.internet.InternetAddress;
import java.time.Duration;
import java.util.Locale;
import java.util.Objects;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * Email SMTP 出站配置（{@code platform.delivery.email}）。只提供经过核对的供应商配置档，
 * 不接受任意 host/port，以免意外把租户联系数据交给错误的 SMTP 主机。
 *
 * <p>credential 只应由环境变量或 secret 文件注入，绝不写入仓库、API 响应或日志。Gmail 和网易
 * 使用客户端授权码；Outlook.com / Hotmail 的配置档要求 OAuth2 access token。
 */
@ConfigurationProperties("platform.delivery.email")
public record DeliveryEmailProperties(
        @DefaultValue("false") boolean enabled,
        @DefaultValue("") String provider,
        @DefaultValue("") String authMode,
        @DefaultValue("") String fromAddress,
        @DefaultValue("") String username,
        @DefaultValue("") String credential,
        @DefaultValue("PT10S") Duration connectTimeout,
        @DefaultValue("PT30S") Duration readTimeout,
        @DefaultValue("PT30S") Duration writeTimeout) {

    enum Profile {
        GMAIL("gmail", "smtp.gmail.com", 587, TlsMode.STARTTLS),
        OUTLOOK_COM("outlook-com", "smtp-mail.outlook.com", 587, TlsMode.STARTTLS),
        NETEASE_163("netease-163", "smtp.163.com", 465, TlsMode.IMPLICIT_TLS);

        private final String code;
        private final String host;
        private final int port;
        private final TlsMode tlsMode;

        Profile(String code, String host, int port, TlsMode tlsMode) {
            this.code = code;
            this.host = host;
            this.port = port;
            this.tlsMode = tlsMode;
        }

        static Profile require(String value) {
            String normalized = value == null ? "" : value.trim().toLowerCase(Locale.ROOT).replace('_', '-');
            for (Profile profile : values()) {
                if (profile.code.equals(normalized)) {
                    return profile;
                }
            }
            throw new IllegalArgumentException("platform.delivery.email.provider must be gmail, outlook-com, or netease-163");
        }

        String code() { return code; }

        String host() { return host; }

        int port() { return port; }

        TlsMode tlsMode() { return tlsMode; }

        boolean accepts(AuthMode mode) {
            return switch (this) {
                case GMAIL -> mode == AuthMode.APP_PASSWORD || mode == AuthMode.OAUTH2;
                case OUTLOOK_COM -> mode == AuthMode.OAUTH2;
                case NETEASE_163 -> mode == AuthMode.APP_PASSWORD;
            };
        }
    }

    enum TlsMode { STARTTLS, IMPLICIT_TLS }

    enum AuthMode {
        APP_PASSWORD("app-password"),
        OAUTH2("oauth2");

        private final String code;

        AuthMode(String code) {
            this.code = code;
        }

        static AuthMode require(String value) {
            String normalized = value == null ? "" : value.trim().toLowerCase(Locale.ROOT).replace('_', '-');
            for (AuthMode mode : values()) {
                if (mode.code.equals(normalized)) {
                    return mode;
                }
            }
            throw new IllegalArgumentException("platform.delivery.email.auth-mode must be app-password or oauth2");
        }

        String code() { return code; }
    }

    public DeliveryEmailProperties {
        provider = safe(provider);
        authMode = safe(authMode);
        fromAddress = safe(fromAddress);
        username = safe(username);
        credential = safe(credential);
        requirePositive(connectTimeout, "connect-timeout");
        requirePositive(readTimeout, "read-timeout");
        requirePositive(writeTimeout, "write-timeout");
    }

    Resolved resolve(Duration sendLease) {
        if (!enabled) {
            throw new IllegalStateException("email delivery is disabled");
        }
        Profile selected = Profile.require(provider);
        AuthMode selectedAuthMode = AuthMode.require(authMode);
        if (!selected.accepts(selectedAuthMode)) {
            throw new IllegalArgumentException("platform.delivery.email.auth-mode is not supported by provider "
                    + selected.code());
        }
        requireAddress(fromAddress, "from-address");
        requireAddress(username, "username");
        if (!fromAddress.equalsIgnoreCase(username)) {
            throw new IllegalArgumentException("platform.delivery.email.username must match from-address");
        }
        if (credential.isBlank()) {
            throw new IllegalArgumentException("platform.delivery.email.credential must be configured by an environment variable or secret");
        }
        Duration timeoutBudget = connectTimeout.plus(readTimeout).plus(writeTimeout);
        if (sendLease == null || sendLease.compareTo(timeoutBudget) <= 0) {
            throw new IllegalArgumentException("platform.delivery.send-lease must exceed the total email SMTP timeouts");
        }
        return new Resolved(selected, selectedAuthMode, fromAddress, username, credential, connectTimeout, readTimeout, writeTimeout,
                domain(fromAddress));
    }

    record Resolved(Profile profile, AuthMode authMode, String fromAddress, String username, String credential,
            Duration connectTimeout, Duration readTimeout, Duration writeTimeout, String messageIdDomain) {
        Resolved {
            Objects.requireNonNull(profile);
        }
    }

    private static String safe(String value) {
        return value == null ? "" : value.trim();
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("platform.delivery.email." + name + " must be positive");
        }
    }

    private static void requireAddress(String value, String name) {
        try {
            InternetAddress address = new InternetAddress(value, true);
            address.validate();
        } catch (AddressException e) {
            throw new IllegalArgumentException("platform.delivery.email." + name + " must be a valid email address");
        }
    }

    private static String domain(String address) {
        int at = address.lastIndexOf('@');
        return address.substring(at + 1).toLowerCase(Locale.ROOT);
    }
}
