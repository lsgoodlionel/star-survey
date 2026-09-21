package cn.mjy.platform.engine;

/**
 * 一批事件的处理结果。{@code received = accepted + duplicates}。
 *
 * @param received   本批事件数
 * @param accepted   首次收到并已记入收件箱的事件数（是否改变投影取决于先后顺序）
 * @param duplicates 此前已收到、本次未产生任何效果的事件数
 */
public record IngestionResult(int received, int accepted, int duplicates) {

    static final IngestionResult EMPTY = new IngestionResult(0, 0, 0);

    IngestionResult plus(IngestionResult other) {
        return new IngestionResult(received + other.received, accepted + other.accepted,
                duplicates + other.duplicates);
    }
}
