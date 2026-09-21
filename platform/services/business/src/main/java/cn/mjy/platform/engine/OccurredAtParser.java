package cn.mjy.platform.engine;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.Optional;

/**
 * 解释信封里的 {@code occurredAt}。
 *
 * <p>引擎写入时用 {@code gmdate('Y-m-d H:i:s')}，中继从 datetime 列原样读出，因此线上格式是
 * {@code 2026-09-21 07:00:00}：UTC、无时区后缀。这里按 UTC 解释。
 * 同时接受带偏移的 ISO-8601（例如 {@code 2026-09-21T15:00:00+08:00}），便于发送端将来改成明确的格式。
 */
final class OccurredAtParser {

    private static final DateTimeFormatter ENGINE_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private OccurredAtParser() {
    }

    static Optional<Instant> parse(String text) {
        try {
            return Optional.of(LocalDateTime.parse(text, ENGINE_FORMAT).toInstant(ZoneOffset.UTC));
        } catch (DateTimeParseException notEngineFormat) {
            return parseIso(text);
        }
    }

    private static Optional<Instant> parseIso(String text) {
        try {
            return Optional.of(OffsetDateTime.parse(text).toInstant());
        } catch (DateTimeParseException notIso) {
            return Optional.empty();
        }
    }
}
