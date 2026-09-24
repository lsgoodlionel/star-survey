package cn.mjy.platform.response;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;

/**
 * 一个作业的附件字节在导出存储里的落脚点（ADR 0015 增补四）。
 *
 * <p>每一份附件是**一个独立对象**，键由自然键算出（确定性哈希），所以：
 *
 * <ul>
 *   <li><b>内存有界</b>：取件、收尾都是一份一份流式过去，任何时刻只持有一个缓冲区；</li>
 *   <li><b>失败项可重试</b>：已经落盘的那一份下次跳过，只补没取到的；</li>
 *   <li><b>崩溃恢复后逐字节相同</b>：键与包内路径都只由自然键决定，重跑落在同一个位置。</li>
 * </ul>
 *
 * <p>取不到且**永久取不到**的那一份留一个墓碑对象（键加 {@code .gone} 后缀，内容是原因码）：
 * 重跑不再去问引擎，收尾时按它写出「缺这一份、为什么缺」。既不静默丢失，也不让一份缺失的附件
 * 把整包拖垮（R06-07）。
 */
final class ExportAttachments {

    /** 墓碑里允许的原因码长度上限；读回时超出即当作未知原因。 */
    static final int MAX_REASON_BYTES = 32;
    static final String REASON_NOT_FOUND = "not_found";
    static final String REASON_TOO_LARGE = "too_large";
    static final String REASON_UNKNOWN = "unknown";

    private static final String SEGMENT = "/att/";
    private static final String TOMBSTONE_SUFFIX = ".gone";
    /** 键里保留的十六进制位数：128 位足够让自然键之间不撞。 */
    private static final int KEY_HEX_LENGTH = 32;

    /** 一份附件的落地结果，收尾时写进包里的 files 表。 */
    record Outcome(boolean stored, long size, String reason) {

        static final String STORED = "stored";
        static final String ABSENT = "absent";

        String status() {
            return stored ? STORED : ABSENT;
        }
    }

    private final ExportFileStore files;
    private final String jobPrefix;

    ExportAttachments(ExportFileStore files, String jobPrefix) {
        this.files = files;
        this.jobPrefix = jobPrefix;
    }

    String key(ExportAttachmentRef ref) {
        return jobPrefix + SEGMENT + hash(ref);
    }

    String tombstoneKey(ExportAttachmentRef ref) {
        return key(ref) + TOMBSTONE_SUFFIX;
    }

    /** 这一份已经有结论了吗（取到了，或者已判定永久缺失）。 */
    boolean resolved(ExportAttachmentRef ref) {
        return files.exists(key(ref)) || files.exists(tombstoneKey(ref));
    }

    /** 记下"永久取不到"。原因码只进包里的 files 表，不带文件名、不带作答值。 */
    void markAbsent(ExportAttachmentRef ref, String reason) throws IOException {
        try (ExportFileStore.Upload upload = files.create(tombstoneKey(ref))) {
            upload.stream().write(reason.getBytes(StandardCharsets.US_ASCII));
            upload.commit();
        }
    }

    Outcome outcome(ExportAttachmentRef ref) throws IOException {
        String key = key(ref);
        if (files.exists(key)) {
            return new Outcome(true, files.size(key), "");
        }
        return new Outcome(false, 0, reason(ref));
    }

    InputStream open(ExportAttachmentRef ref) throws IOException {
        return files.open(key(ref));
    }

    private String reason(ExportAttachmentRef ref) throws IOException {
        String tombstone = tombstoneKey(ref);
        if (!files.exists(tombstone)) {
            // 既没有字节也没有墓碑：分批阶段本该给每一份都留下结论，走到这里说明有 bug。
            // 如实写 unknown，不假装这一份从来不存在。
            return REASON_UNKNOWN;
        }
        try (InputStream in = files.open(tombstone)) {
            byte[] bytes = in.readNBytes(MAX_REASON_BYTES + 1);
            String reason = new String(bytes, StandardCharsets.US_ASCII);
            return reason.isBlank() || bytes.length > MAX_REASON_BYTES ? REASON_UNKNOWN : reason;
        }
    }

    /** 自然键的确定性哈希：同一份附件在重跑、恢复后落在同一个键上。 */
    private static String hash(ExportAttachmentRef ref) {
        String identity = ref.version() + "|" + ref.engineSid() + "|" + ref.responseId() + "|" + ref.fieldname()
                + "|" + ref.index();
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(identity.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, KEY_HEX_LENGTH).toLowerCase(Locale.ROOT);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required by every JDK", e);
        }
    }
}
