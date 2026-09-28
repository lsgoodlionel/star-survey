package cn.mjy.platform.response;

import cn.mjy.platform.engine.ResponseState;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Arrays;

/**
 * 明细列表的一次查询：复合筛选条件 ＋ 键集翻页位置 ＋ 每页条数（R06-01，ADR 0013 增补一）。
 *
 * <p>做成一个不可变值对象，是为了让**所有边界校验只发生在一处**：非法的状态、时刻、版本号、
 * 游标与条数在构造时就抛 {@link InvalidResponseQueryException}，服务层拿到的实例一定是合法的。
 *
 * <p>筛选条件全部会下推到投影表的 SQL（见 {@code ResponsePager}）。这一点不是实现细节：
 * 只有下推才能保证「页是满的」与键集游标的语义不变——先取一页再在内存里筛，会让页变短、
 * 让 {@code nextCursor} 的含义在"最后一行"与"最后一行命中的"之间摇摆。
 *
 * <p>时间区间是**闭区间**，两端都可省略。{@code completedFrom/To} 只会命中已完成的答卷
 * （未完成的没有完成时刻）。
 *
 * <p><b>筛选不改变无漏重的保证</b>：排序仍是不可变自然键 (版本, 代次, 答卷号)。
 * {@code startedFrom/To} 的谓词落在不可变的 {@code first_event_at} 上，翻页期间不会翻转；
 * {@code state} 与 {@code completedFrom/To} 的谓词会随答卷推进而翻转，
 * 这与 ADR 0013 决定 1 里"按当前状态筛选"固有的语义完全一样，不是筛选新引入的。
 */
public record ResponseQuery(
        ResponseState state,
        Integer version,
        Instant startedFrom,
        Instant startedTo,
        Instant completedFrom,
        Instant completedTo,
        ResponseCursor after,
        int limit) {

    public static final int DEFAULT_LIMIT = 50;
    public static final int MAX_LIMIT = 200;

    /** 不带任何条件的第一页。 */
    public static final ResponseQuery ALL =
            new ResponseQuery(null, null, null, null, null, null, null, DEFAULT_LIMIT);

    public ResponseQuery {
        requireOrdered("startedFrom", startedFrom, "startedTo", startedTo);
        requireOrdered("completedFrom", completedFrom, "completedTo", completedTo);
        if (version != null && version < 1) {
            throw invalid("version must be a positive published version number");
        }
        if (limit < 1 || limit > MAX_LIMIT) {
            throw invalid("limit must be between 1 and " + MAX_LIMIT);
        }
    }

    /** HTTP 查询参数 → 查询对象；每个参数都可缺省。 */
    public static ResponseQuery parse(String state, Integer version, String startedFrom, String startedTo,
            String completedFrom, String completedTo, String cursor, Integer limit) {
        return new ResponseQuery(parseState(state), version,
                parseInstant("startedFrom", startedFrom), parseInstant("startedTo", startedTo),
                parseInstant("completedFrom", completedFrom), parseInstant("completedTo", completedTo),
                parseCursor(cursor), limit == null ? DEFAULT_LIMIT : limit);
    }

    public ResponseQuery withState(ResponseState value) {
        return new ResponseQuery(value, version, startedFrom, startedTo, completedFrom, completedTo, after, limit);
    }

    public ResponseQuery withVersion(int value) {
        return new ResponseQuery(state, value, startedFrom, startedTo, completedFrom, completedTo, after, limit);
    }

    public ResponseQuery withStarted(Instant from, Instant to) {
        return new ResponseQuery(state, version, from, to, completedFrom, completedTo, after, limit);
    }

    public ResponseQuery withCompleted(Instant from, Instant to) {
        return new ResponseQuery(state, version, startedFrom, startedTo, from, to, after, limit);
    }

    /** {@code null} 或空串表示第一页。 */
    public ResponseQuery withCursor(String cursor) {
        return new ResponseQuery(state, version, startedFrom, startedTo, completedFrom, completedTo,
                parseCursor(cursor), limit);
    }

    public ResponseQuery withLimit(int value) {
        return new ResponseQuery(state, version, startedFrom, startedTo, completedFrom, completedTo, after, value);
    }

    /** 该版本是否要参与本次扫描（未指定版本时全部参与）。 */
    public boolean coversVersion(int candidate) {
        return version == null || version == candidate;
    }

    private static void requireOrdered(String fromName, Instant from, String toName, Instant to) {
        if (from != null && to != null && from.isAfter(to)) {
            throw invalid(fromName + " must not be after " + toName);
        }
    }

    private static ResponseState parseState(String state) {
        if (state == null || state.isEmpty()) {
            return null;
        }
        return Arrays.stream(ResponseState.values())
                .filter(s -> s.dbValue().equals(state))
                .findFirst()
                .orElseThrow(() -> invalid("state must be one of in_progress, engine_completed, deleted"));
    }

    private static Instant parseInstant(String name, String text) {
        if (text == null || text.isEmpty()) {
            return null;
        }
        try {
            return Instant.parse(text);
        } catch (DateTimeParseException e) {
            throw invalid(name + " must be an ISO-8601 instant such as 2026-09-20T00:00:00Z");
        }
    }

    private static ResponseCursor parseCursor(String cursor) {
        return cursor == null || cursor.isEmpty() ? null : ResponseCursor.decode(cursor);
    }

    private static InvalidResponseQueryException invalid(String message) {
        return new InvalidResponseQueryException(message);
    }
}
