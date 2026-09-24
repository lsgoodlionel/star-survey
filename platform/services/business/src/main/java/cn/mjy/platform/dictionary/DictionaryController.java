package cn.mjy.platform.dictionary;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 租户（与作答页）读字典。只读，只看得见已发布的版本。
 *
 * <p>这里是「树有数千节点、作答页不能一次全下发」的落点：一次一层一页。
 */
@RestController
@RequestMapping("/v1/dictionaries")
class DictionaryController {

    private static final int DEFAULT_PAGE_SIZE = 50;

    private final CurrentTenant currentTenant;
    private final DictionaryCatalog catalog;

    DictionaryController(CurrentTenant currentTenant, DictionaryCatalog catalog) {
        this.currentTenant = currentTenant;
        this.catalog = catalog;
    }

    @GetMapping
    List<DictionaryView> list() {
        return catalog.list(currentTenant.require());
    }

    @GetMapping("/{code}/versions/{version}")
    DictionaryVersionView version(@PathVariable String code, @PathVariable String version) {
        return catalog.version(currentTenant.require(), code, version);
    }

    /** {@code parent} 缺省取根那一层。 */
    @GetMapping("/{code}/versions/{version}/nodes")
    DictionaryPage nodes(@PathVariable String code, @PathVariable String version,
            @RequestParam(required = false) String parent,
            @RequestParam(defaultValue = "0") int offset,
            @RequestParam(defaultValue = "" + DEFAULT_PAGE_SIZE) int size) {
        return catalog.children(currentTenant.require(), code, version, parent, offset, size);
    }

    @GetMapping("/{code}/versions/{version}/search")
    List<DictionaryNodeView> search(@PathVariable String code, @PathVariable String version,
            @RequestParam String q,
            @RequestParam(defaultValue = "" + DictionaryLimits.MAX_SEARCH_HITS) int size) {
        return catalog.search(currentTenant.require(), code, version, q, size);
    }

    /** 搜索命中一个区之后，用它把省 / 市 / 区三级一次性摆好。 */
    @GetMapping("/{code}/versions/{version}/nodes/{nodeCode}/path")
    List<DictionaryNodeView> path(@PathVariable String code, @PathVariable String version,
            @PathVariable String nodeCode) {
        return catalog.path(currentTenant.require(), code, version, nodeCode);
    }
}
