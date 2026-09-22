package cn.mjy.platform.response;

import cn.mjy.platform.engine.ResponseProjection;
import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.engine.ResponseProjectionQuery.Position;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ResponseSources.Source;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;

/**
 * 跨版本的键集翻页：按版本号升序依次扫各来源，每个来源内按 (代次, 答卷号) 升序。
 * 必须在 {@code TenantScope} 内调用（投影表行级安全）。
 */
@Component
class ResponsePager {

    /** 一页投影行，外加每个出现过的来源的当前代次（决定哪些行能去引擎取作答）。 */
    record Slice(List<Entry> entries, boolean hasMore, Map<Source, String> latestGenerations) {

        Slice {
            entries = List.copyOf(entries);
            latestGenerations = Map.copyOf(latestGenerations);
        }
    }

    record Entry(Source source, ResponseProjection projection) {

        ResponseCursor cursor() {
            return new ResponseCursor(source.version(), projection.key().generation(), projection.key().responseId());
        }
    }

    private final ResponseProjectionQuery projections;

    ResponsePager(ResponseProjectionQuery projections) {
        this.projections = projections;
    }

    /** 多取一行判断是否还有下一页，避免调用方多翻一次空页。 */
    Slice slice(List<Source> sources, ResponseState state, ResponseCursor after, int size) {
        List<Entry> entries = new ArrayList<>();
        Map<Source, String> generations = new HashMap<>();
        for (Source source : sources) {
            if (after != null && source.version() < after.version()) {
                continue;
            }
            Position from = after != null && source.version() == after.version()
                    ? new Position(after.generation(), after.responseId()) : null;
            List<ResponseProjection> rows = projections.page(source.engineInstanceId(), source.engineSid(), state,
                    from, size + 1 - entries.size());
            rows.forEach(row -> entries.add(new Entry(source, row)));
            if (!rows.isEmpty()) {
                projections.latestGeneration(source.engineInstanceId(), source.engineSid())
                        .ifPresent(g -> generations.put(source, g));
            }
            if (entries.size() > size) {
                return new Slice(entries.subList(0, size), true, generations);
            }
        }
        return new Slice(entries, false, generations);
    }
}
