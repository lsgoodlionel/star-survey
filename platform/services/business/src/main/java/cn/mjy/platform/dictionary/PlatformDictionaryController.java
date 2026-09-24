package cn.mjy.platform.dictionary;

import cn.mjy.platform.tenant.PlatformOperatorGuard;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * 运营侧的字典维护接口。整个 {@code /v1/platform/**} 由 {@code PlatformWebConfig} 的拦截器
 * 要求 {@code platform_operator} 角色；这里再调一次 guard 只是为了拿到调用者标识写审计。
 *
 * <p>这也是「数据从哪来」的答案：<b>仓库不附带任何行政区划数据集</b>（与 ADR 0016 对 GeoIP 的口径一致，
 * 数据集各有各的许可与署名义务），由交付方通过这些端点导入（ADR 0019 决定 5）。
 */
@RestController
@RequestMapping("/v1/platform/dictionaries")
class PlatformDictionaryController {

    private final DictionaryService dictionaries;
    private final PlatformOperatorGuard guard;

    PlatformDictionaryController(DictionaryService dictionaries, PlatformOperatorGuard guard) {
        this.dictionaries = dictionaries;
        this.guard = guard;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    DictionaryView create(@RequestBody CreateDictionary request) {
        String operator = guard.requireOperator();
        return dictionaries.createDictionary(operator, newTraceId(), request.code(), request.name(),
                request.maxDepth() == null ? 0 : request.maxDepth());
    }

    @GetMapping
    List<DictionaryView> list() {
        guard.requireOperator();
        return dictionaries.list();
    }

    @GetMapping("/{code}/versions")
    List<DictionaryVersionView> versions(@PathVariable String code) {
        guard.requireOperator();
        return dictionaries.versions(code);
    }

    @PostMapping("/{code}/versions")
    @ResponseStatus(HttpStatus.CREATED)
    DictionaryVersionView createDraft(@PathVariable String code, @RequestBody CreateVersion request) {
        String operator = guard.requireOperator();
        return dictionaries.createDraft(operator, newTraceId(), code, request.version());
    }

    /** 整棵树一次灌进来，覆盖这一版草稿原有的节点。 */
    @PostMapping("/{code}/versions/{version}/nodes")
    DictionaryVersionView loadNodes(@PathVariable String code, @PathVariable String version,
            @RequestBody LoadNodes request) {
        String operator = guard.requireOperator();
        List<DictionaryNodeDraft> nodes = request.nodes() == null ? List.of()
                : request.nodes().stream()
                        .map(node -> new DictionaryNodeDraft(node.code(), node.parentCode(), node.label()))
                        .toList();
        int loaded = dictionaries.loadNodes(operator, newTraceId(), code, version, nodes);
        DictionaryVersionView draft = dictionaries.version(code, version);
        // 草稿的 node_count 要到发布才落库，这里直接回显本次灌了多少条。
        return new DictionaryVersionView(draft.dictionaryCode(), draft.version(), draft.status(),
                draft.digest(), loaded, draft.publishedAt());
    }

    @PostMapping("/{code}/versions/{version}/publish")
    DictionaryVersionView publish(@PathVariable String code, @PathVariable String version) {
        String operator = guard.requireOperator();
        return dictionaries.publishVersion(operator, newTraceId(), code, version);
    }

    private static String newTraceId() {
        return UUID.randomUUID().toString();
    }

    public record CreateDictionary(String code, String name, Integer maxDepth) {
    }

    public record CreateVersion(String version) {
    }

    public record LoadNodes(List<NodeBody> nodes) {
    }

    public record NodeBody(String code, String parentCode, String label) {
    }
}
