package cn.mjy.platform.response;

import java.util.List;

/**
 * 一页答卷明细。
 *
 * @param nextCursor        下一页游标；最后一页为 {@code null}
 * @param sensitiveRevealed 调用方能否看到敏感列明文（否则已遮蔽）
 */
public record ResponsePage(List<ResponseRow> items, String nextCursor, boolean sensitiveRevealed) {

    public ResponsePage {
        items = List.copyOf(items);
    }
}
