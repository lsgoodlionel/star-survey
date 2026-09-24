package cn.mjy.platform.response;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 一份答卷的全部内容：答卷表的那一行，加上属于它的附件清单行与扩展副表单元格行。
 * 表格格式用不上这个视图（它们按表出列），逐份成文的格式（Word / PDF）要的就是它。
 *
 * <p>分片里三类行本来就按答卷交替存放（{@link ExportPartCodec}），因此这个视图是<b>流式</b>的：
 * 任何时刻只持有一份答卷，与总答卷数无关。
 *
 * @param cells       答卷表的一行，下标与 {@link ExportContent#codes()} 对应；空单元格为 {@code null}
 *                    （与 {@link ExportSheet} 的行同一口径，写出方自己决定怎么显示）
 * @param attachments 附件清单行，列序同 {@link ExportLayout#ATTACHMENT_HEADER}
 * @param extensions  扩展副表单元格行，列序同 {@link ExportLayout#EXTENSION_HEADER}
 */
record ExportRecord(List<String> cells, List<List<String>> attachments, List<List<String>> extensions) {

    ExportRecord {
        // 单元格允许为 null（未作答），所以不能用 List.copyOf。
        cells = Collections.unmodifiableList(new ArrayList<>(cells));
        attachments = List.copyOf(attachments);
        extensions = List.copyOf(extensions);
    }

    /** 按顺序把每份答卷交给 sink；可以被调用多次，每次都从头重放。 */
    @FunctionalInterface
    interface Source {
        void forEach(Sink sink) throws IOException;
    }

    @FunctionalInterface
    interface Sink {
        void accept(ExportRecord record) throws IOException;
    }
}
