package cn.mjy.platform.dictionary;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.springframework.stereotype.Service;

/**
 * 字典的读取面：租户（与作答页）能对一本已发布的字典做的四件事——按父节点翻页、按关键字搜、
 * 把一个节点还原成整条路径，以及<b>判定一条路径算不算数</b>。
 *
 * <p>只看得见已发布的版本。草稿对租户等同不存在，否则运营改到一半的字典会被问卷引用上。
 *
 * <p>分页不是优化：行政区划有数千个节点，作答页一次全下发既慢又没必要，
 * 作答者一次只看一层（ADR 0019 决定 4）。
 */
@Service
public class DictionaryCatalog {

    private final TenantScope tenantScope;
    private final DictionaryRepository dictionaries;

    DictionaryCatalog(TenantScope tenantScope, DictionaryRepository dictionaries) {
        this.tenantScope = tenantScope;
        this.dictionaries = dictionaries;
    }

    /** 至少发布过一版的字典。 */
    public List<DictionaryView> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), dictionaries::publishedDictionaries);
    }

    public DictionaryVersionView version(TenantContext ctx, String code, String version) {
        return tenantScope.call(ctx.tenantId(), () -> requirePublished(code, version));
    }

    /**
     * 某一层的一页。{@code parentCode} 为空取根那一层。
     *
     * @param limit 超过 {@link DictionaryLimits#MAX_PAGE_SIZE} 时拒绝，<b>不</b>悄悄截断——
     *              悄悄截断会让调用方以为「这一层就这么多」。
     */
    public DictionaryPage children(TenantContext ctx, String code, String version, String parentCode,
            int offset, int limit) {
        int page = requirePageSize(limit);
        int from = requireOffset(offset);
        String parent = blankToNull(parentCode);
        return tenantScope.call(ctx.tenantId(), () -> {
            requirePublished(code, version);
            return new DictionaryPage(dictionaries.children(code, version, parent, from, page),
                    dictionaries.countChildren(code, version, parent));
        });
    }

    /** 按标签包含或代码前缀搜索。空关键字被拒绝：那等于「把整本字典给我」。 */
    public List<DictionaryNodeView> search(TenantContext ctx, String code, String version, String keyword,
            int limit) {
        String clean = requireKeyword(keyword);
        int hits = Math.min(requirePageSize(limit), DictionaryLimits.MAX_SEARCH_HITS);
        return tenantScope.call(ctx.tenantId(), () -> {
            requirePublished(code, version);
            return dictionaries.search(code, version, clean, hits);
        });
    }

    /** 从根到这个节点的整条路径，顺序由浅到深。搜索命中之后要把三级下拉一次性摆好就靠它。 */
    public List<DictionaryNodeView> path(TenantContext ctx, String code, String version, String nodeCode) {
        return tenantScope.call(ctx.tenantId(), () -> {
            requirePublished(code, version);
            return pathOf(code, version, nodeCode);
        });
    }

    /**
     * <b>服务端的路径判定</b>：这一串代码在这一版字典里是不是一条真实存在、自上而下连得起来的路径。
     *
     * <p>不带租户上下文，因为字典是全局参考数据，判定与谁在问无关；插件侧有一份等价实现
     * （引擎上没有平台连接），两边的规则写在契约里，靠同一组篡改用例钉住。
     *
     * <p>四条规则，缺一不可：
     * <ol>
     *   <li>非空，且不超过这本字典的层数；</li>
     *   <li>每一级的代码都在<b>这一版</b>里（跨版本的节点因此进不来）；</li>
     *   <li>第一级必须是根（{@code depth == 1}）；</li>
     *   <li>后一级的父节点必须<b>恰好</b>是前一级（跨省的市因此进不来）。</li>
     * </ol>
     */
    public DictionaryPathCheck checkPath(String code, String version, List<String> codes) {
        Objects.requireNonNull(codes, "codes");
        if (codes.isEmpty()) {
            return DictionaryPathCheck.failed("路径不能为空");
        }
        if (codes.size() > DictionaryLimits.MAX_DEPTH) {
            return DictionaryPathCheck.failed("路径层数超过 " + DictionaryLimits.MAX_DEPTH);
        }
        // 刻意不开 TenantScope：字典表没有行级安全，而本方法可能在别的模块的租户事务里被调用。
        // TenantScope 用的是 SET LOCAL，在嵌套调用里切租户会把外层事务剩下的语句一并改掉（见 SurveyDictionaries）。
        if (dictionaries.findVersion(code, version)
                .filter(found -> found.status() == DictionaryVersionStatus.PUBLISHED).isEmpty()) {
            return DictionaryPathCheck.failed("字典 " + code + " 没有已发布的 " + version + " 版");
        }
        return walk(codes, byCode(dictionaries.nodesIn(code, version, codes)));
    }

    // ---------------------------------------------------------------- 内部

    /** 已在作用域内时调用。 */
    private List<DictionaryNodeView> pathOf(String code, String version, String nodeCode) {
        List<DictionaryNodeView> path = new ArrayList<>();
        String cursor = nodeCode;
        // 层数有硬上限，所以逐级往上最多走 MAX_DEPTH 次，不会打转。
        while (cursor != null && path.size() <= DictionaryLimits.MAX_DEPTH) {
            DictionaryNodeView node = dictionaries.node(code, version, cursor).orElseThrow(() ->
                    new DictionaryNotFoundException("dictionary node not found: " + code + "/" + version
                            + "/" + nodeCode));
            path.add(node);
            cursor = node.parentCode();
        }
        return path.reversed();
    }

    private static DictionaryPathCheck walk(List<String> codes, Map<String, DictionaryNodeView> found) {
        String parent = null;
        for (int level = 0; level < codes.size(); level++) {
            String code = codes.get(level);
            DictionaryNodeView node = found.get(code);
            if (node == null) {
                return DictionaryPathCheck.failed("第 " + (level + 1) + " 级的 " + code + " 不在这一版字典里");
            }
            if (node.depth() != level + 1) {
                return DictionaryPathCheck.failed("第 " + (level + 1) + " 级的 " + code
                        + " 在字典里是第 " + node.depth() + " 层");
            }
            if (!Objects.equals(node.parentCode(), parent)) {
                return DictionaryPathCheck.failed("第 " + (level + 1) + " 级的 " + code
                        + " 不属于上一级" + (parent == null ? "（它不是根）" : " " + parent));
            }
            parent = code;
        }
        return DictionaryPathCheck.passed();
    }

    private static Map<String, DictionaryNodeView> byCode(List<DictionaryNodeView> nodes) {
        Map<String, DictionaryNodeView> byCode = new HashMap<>();
        for (DictionaryNodeView node : nodes) {
            byCode.put(node.code(), node);
        }
        return byCode;
    }

    private DictionaryVersionView requirePublished(String code, String version) {
        return dictionaries.findVersion(code, version)
                .filter(found -> found.status() == DictionaryVersionStatus.PUBLISHED)
                .orElseThrow(() -> new DictionaryNotFoundException(
                        "published dictionary version not found: " + code + "/" + version));
    }

    private static int requirePageSize(int limit) {
        if (limit < 1 || limit > DictionaryLimits.MAX_PAGE_SIZE) {
            throw new InvalidDictionaryRequestException(
                    "size 必须在 1 到 " + DictionaryLimits.MAX_PAGE_SIZE + " 之间");
        }
        return limit;
    }

    private static int requireOffset(int offset) {
        if (offset < 0) {
            throw new InvalidDictionaryRequestException("offset 不能为负");
        }
        return offset;
    }

    private static String requireKeyword(String keyword) {
        String clean = keyword == null ? "" : keyword.trim();
        if (clean.isEmpty()) {
            throw new InvalidDictionaryRequestException("q 不能为空");
        }
        if (clean.length() > DictionaryLimits.MAX_KEYWORD) {
            throw new InvalidDictionaryRequestException("q 最长 " + DictionaryLimits.MAX_KEYWORD + " 个字符");
        }
        return clean;
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
