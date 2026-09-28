package cn.mjy.platform.response;

import java.util.List;

/**
 * 附件清单里的一行所指的那一份文件（ADR 0015 增补四）。附件包格式按它取字节、定包内路径。
 *
 * <p>自然键是 (版本, 引擎问卷号, 答卷号, 引擎列名, 列内序号)——序号是**每一列内**从 1 起，
 * 所以同一份答卷的两道上传题都会有序号 1，列名因此不能省。
 *
 * @param index      列内序号，从 1 起
 * @param storedName 引擎给这份文件起的存储名（{@code fu_…}），由引擎生成、不含作答者文本
 */
record ExportAttachmentRef(int version, long engineSid, long responseId, String fieldname, int index,
        String storedName) {

    private static final int MAX_PATH_NAME = 80;
    private static final String FALLBACK_NAME = "file";

    /** 从附件清单的一行读出（列序同 {@link ExportLayout#ATTACHMENT_HEADER}）。 */
    static ExportAttachmentRef of(List<String> row) {
        if (row.size() != ExportLayout.ATTACHMENT_HEADER.size()) {
            throw new IllegalArgumentException("attachment row has " + row.size() + " cells");
        }
        return new ExportAttachmentRef(Integer.parseInt(cell(row, "version")), Long.parseLong(cell(row, "sid")),
                Long.parseLong(cell(row, "responseid")), cell(row, "fieldname"),
                Integer.parseInt(cell(row, "index")), cell(row, "storedname"));
    }

    private static String cell(List<String> row, String column) {
        return row.get(ExportLayout.ATTACHMENT_HEADER.indexOf(column));
    }

    /**
     * 包内路径：{@code files/<问卷号>-<答卷号>/<列名>-<序号>-<存储名>}。
     *
     * <p>问卷号在前是因为多版本联合导出里两个版本的答卷号会重号；列名在里面是因为序号只在列内唯一。
     * 存储名只保留 {@code [A-Za-z0-9._-]}，其余一律换成下划线并截断——它本该是引擎生成的
     * {@code fu_…}，但导出只把引擎给的值当文本，不假设它一定合规。
     */
    String pathInZip() {
        return "files/" + engineSid + "-" + responseId + "/" + sanitize(fieldname) + "-" + index + "-"
                + sanitize(storedName);
    }

    private static String sanitize(String value) {
        if (value == null || value.isEmpty()) {
            return FALLBACK_NAME;
        }
        StringBuilder safe = new StringBuilder(Math.min(value.length(), MAX_PATH_NAME));
        for (int i = 0; i < value.length() && safe.length() < MAX_PATH_NAME; i++) {
            char c = value.charAt(i);
            safe.append(isSafe(c) ? c : '_');
        }
        String result = safe.toString();
        return result.isBlank() || ".".equals(result) || "..".equals(result) ? FALLBACK_NAME : result;
    }

    private static boolean isSafe(char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
                || c == '.' || c == '_' || c == '-';
    }
}
