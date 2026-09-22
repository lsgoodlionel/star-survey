package cn.mjy.platform.response;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Objects;

/**
 * 明细翻页游标：上一页最后一行的 (版本号, 代次, 答卷号)。三者都是不可变的自然键，
 * 所以翻页期间的新写入不会让已翻过的行重复出现（ADR 0013 决定 1）。
 *
 * <p>编码为 URL 安全的 Base64 文本 {@code v1|版本|答卷号|代次}，对调用方不透明；
 * 代次放最后，内容里出现分隔符也不影响解析。游标不是凭证：它只决定从哪里继续，权限每页重新判定。
 */
public record ResponseCursor(int version, String generation, long responseId) {

    private static final String PREFIX = "v1";
    private static final String SEPARATOR = "|";
    private static final int PARTS = 4;
    private static final int MAX_GENERATION_LENGTH = 64;
    private static final int MAX_ENCODED_LENGTH = 256;

    public ResponseCursor {
        Objects.requireNonNull(generation, "generation");
    }

    public String encode() {
        String raw = String.join(SEPARATOR, PREFIX, Integer.toString(version), Long.toString(responseId), generation);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw.getBytes(StandardCharsets.UTF_8));
    }

    public static ResponseCursor decode(String text) {
        if (text == null || text.isEmpty() || text.length() > MAX_ENCODED_LENGTH) {
            throw invalid();
        }
        String[] parts = raw(text).split("\\|", PARTS);
        if (parts.length != PARTS || !PREFIX.equals(parts[0])) {
            throw invalid();
        }
        int version = positiveInt(parts[1]);
        long responseId = positiveLong(parts[2]);
        String generation = parts[3];
        if (generation.isEmpty() || generation.length() > MAX_GENERATION_LENGTH) {
            throw invalid();
        }
        return new ResponseCursor(version, generation, responseId);
    }

    private static String raw(String text) {
        try {
            return new String(Base64.getUrlDecoder().decode(text), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            throw invalid();
        }
    }

    private static int positiveInt(String value) {
        long parsed = positiveLong(value);
        if (parsed > Integer.MAX_VALUE) {
            throw invalid();
        }
        return (int) parsed;
    }

    private static long positiveLong(String value) {
        try {
            long parsed = Long.parseLong(value);
            if (parsed <= 0) {
                throw invalid();
            }
            return parsed;
        } catch (NumberFormatException e) {
            throw invalid();
        }
    }

    private static InvalidResponseQueryException invalid() {
        return new InvalidResponseQueryException("cursor is malformed");
    }
}
