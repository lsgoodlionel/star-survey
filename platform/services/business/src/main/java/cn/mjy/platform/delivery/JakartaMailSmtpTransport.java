package cn.mjy.platform.delivery;

import jakarta.mail.AuthenticationFailedException;
import jakarta.mail.Message;
import jakarta.mail.MessagingException;
import jakarta.mail.Session;
import jakarta.mail.Transport;
import jakarta.mail.internet.InternetAddress;
import jakarta.mail.internet.MimeMessage;
import java.nio.charset.StandardCharsets;
import java.util.Properties;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Jakarta Mail 的生产 SMTP 传输实现。禁止 debug，且不记录 SMTP 原始响应。 */
final class JakartaMailSmtpTransport implements SmtpTransport {

    private static final Pattern SMTP_STATUS = Pattern.compile("(?:^|\\D)([245]\\d{2})(?:\\D|$)");

    @Override
    public void verify(DeliveryEmailProperties.Resolved config) {
        Transport transport = null;
        try {
            Session session = Session.getInstance(properties(config));
            session.setDebug(false);
            transport = session.getTransport("smtp");
            transport.connect(config.profile().host(), config.profile().port(), config.username(), config.credential());
        } catch (AuthenticationFailedException e) {
            throw new SmtpTransportException(SmtpTransportException.Kind.AUTHENTICATION);
        } catch (MessagingException e) {
            throw new SmtpTransportException(classify(e));
        } finally {
            if (transport != null) try { transport.close(); } catch (MessagingException ignored) { }
        }
    }

    @Override
    public String submit(DeliveryEmailProperties.Resolved config, DeliveryMessage delivery, String messageId) {
        try {
            Session session = Session.getInstance(properties(config));
            session.setDebug(false);
            MimeMessage message = new MimeMessage(session);
            message.setFrom(new InternetAddress(config.fromAddress(), true));
            message.setRecipient(Message.RecipientType.TO, new InternetAddress(delivery.address(), true));
            message.setSubject(delivery.subject() == null ? "" : delivery.subject(), StandardCharsets.UTF_8.name());
            // 正文是可信模板渲染输出，但仍以 text/plain 交付，避免将任意内容解释为 HTML。
            message.setText(delivery.body(), StandardCharsets.UTF_8.name());
            message.setHeader("Message-ID", messageId);
            message.saveChanges();
            Transport.send(message, config.username(), config.credential());
            return messageId;
        } catch (AuthenticationFailedException e) {
            throw new SmtpTransportException(SmtpTransportException.Kind.AUTHENTICATION);
        } catch (MessagingException e) {
            throw new SmtpTransportException(classify(e));
        }
    }

    private static Properties properties(DeliveryEmailProperties.Resolved config) {
        Properties properties = new Properties();
        properties.setProperty("mail.smtp.host", config.profile().host());
        properties.setProperty("mail.smtp.port", Integer.toString(config.profile().port()));
        properties.setProperty("mail.smtp.auth", "true");
        properties.setProperty("mail.smtp.connectiontimeout", milliseconds(config.connectTimeout()));
        properties.setProperty("mail.smtp.timeout", milliseconds(config.readTimeout()));
        properties.setProperty("mail.smtp.writetimeout", milliseconds(config.writeTimeout()));
        properties.setProperty("mail.smtp.ssl.protocols", "TLSv1.3 TLSv1.2");
        properties.setProperty("mail.smtp.ssl.checkserveridentity", "true");
        if (config.profile().tlsMode() == DeliveryEmailProperties.TlsMode.STARTTLS) {
            properties.setProperty("mail.smtp.starttls.enable", "true");
            properties.setProperty("mail.smtp.starttls.required", "true");
        } else {
            properties.setProperty("mail.smtp.ssl.enable", "true");
        }
        if (config.authMode() == DeliveryEmailProperties.AuthMode.OAUTH2) {
            properties.setProperty("mail.smtp.auth.mechanisms", "XOAUTH2");
            properties.setProperty("mail.smtp.auth.login.disable", "true");
            properties.setProperty("mail.smtp.auth.plain.disable", "true");
        }
        return properties;
    }

    private static String milliseconds(java.time.Duration duration) {
        return Long.toString(duration.toMillis());
    }

    private static SmtpTransportException.Kind classify(MessagingException exception) {
        Matcher status = SMTP_STATUS.matcher(String.valueOf(exception.getMessage()));
        if (status.find()) {
            int code = Integer.parseInt(status.group(1));
            if (code >= 400 && code < 500) {
                return SmtpTransportException.Kind.TEMPORARY;
            }
            if (code == 550 || code == 551 || code == 553) {
                return SmtpTransportException.Kind.RECIPIENT_REJECTED;
            }
        }
        return SmtpTransportException.Kind.UNKNOWN;
    }
}
