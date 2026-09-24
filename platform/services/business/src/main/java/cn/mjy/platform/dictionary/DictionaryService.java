package cn.mjy.platform.dictionary;

import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * 运营侧的字典维护：建字典 → 建草稿版本 → 灌节点 → 发布。
 *
 * <p>核心约束只有一条：<b>发布即冻结</b>。已发布的版本不能再灌节点、不能再发布一次，
 * 因为已发布问卷的定义快照里固化着这一版，它一变，当初合法的作答路径就会变成非法路径
 * （ADR 0019 决定 2）。要更新字典就发布新的一版，旧版原样留着。
 *
 * <p>写操作都在控制平面租户的 {@link TenantScope} 里执行——三张表没有行级安全，
 * 但 audit_log 有，运营的动作要有地方落账（与 {@code PlatformTemplateService} 同一做法）。
 */
@Service
public class DictionaryService {

    private final TenantScope tenantScope;
    private final DictionaryRepository dictionaries;
    private final DictionaryAudit audit;

    DictionaryService(TenantScope tenantScope, DictionaryRepository dictionaries, DictionaryAudit audit) {
        this.tenantScope = tenantScope;
        this.dictionaries = dictionaries;
        this.audit = audit;
    }

    public DictionaryView createDictionary(String operator, String traceId, String code, String name,
            int maxDepth) {
        String cleanCode = requireCode(code);
        String cleanName = requireName(name);
        if (maxDepth < 1 || maxDepth > DictionaryLimits.MAX_DEPTH) {
            throw new InvalidDictionaryRequestException(
                    "maxDepth 必须在 1 到 " + DictionaryLimits.MAX_DEPTH + " 之间");
        }
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> {
            try {
                dictionaries.insertDictionary(cleanCode, cleanName, maxDepth, operator);
            } catch (DuplicateKeyException e) {
                throw new DictionaryConflictException("dictionary_code_taken", "字典 " + cleanCode + " 已存在");
            }
            audit.record(operator, traceId, DictionaryAudit.CREATE, cleanCode, "maxDepth=" + maxDepth);
            return required(cleanCode);
        });
    }

    public DictionaryVersionView createDraft(String operator, String traceId, String code, String version) {
        String cleanVersion = requireVersion(version);
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> {
            required(code);
            try {
                dictionaries.insertVersion(code, cleanVersion, operator);
            } catch (DuplicateKeyException e) {
                throw new DictionaryConflictException("dictionary_version_taken",
                        "字典 " + code + " 已经有一版叫 " + cleanVersion);
            }
            audit.record(operator, traceId, DictionaryAudit.DRAFT, code, "version=" + cleanVersion);
            return requiredVersion(code, cleanVersion);
        });
    }

    /**
     * 整棵树一次灌进来，覆盖这一版草稿原有的节点。
     *
     * <p>为什么不做增量追加：叶子标记与深度都要看全树才算得出来，分批导入意味着每一批都要重算全树，
     * 还要处理「批与批之间这一版是什么形状」。一次一棵树，节点数有硬上限，是更简单也更可解释的选择。
     */
    public int loadNodes(String operator, String traceId, String code, String version,
            List<DictionaryNodeDraft> nodes) {
        List<DictionaryNodeDraft> clean = requireNodes(nodes);
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> {
            DictionaryView dictionary = required(code);
            requireDraft(code, version);
            // 在这里就把树算出来：形状不对的导入根本不该落库，等到发布再报错等于让人白填一次。
            List<DictionaryNodeView> resolved = DictionaryTree.resolve(clean, dictionary.maxDepth());
            dictionaries.deleteNodes(code, version);
            dictionaries.insertNodes(code, version, resolved);
            audit.record(operator, traceId, DictionaryAudit.LOAD, code,
                    "version=" + version + " nodes=" + clean.size());
            return clean.size();
        });
    }

    /** 发布：算摘要、冻结这一版、把它设成当前版本（之后新发布的问卷都会固化到它）。 */
    public DictionaryVersionView publishVersion(String operator, String traceId, String code, String version) {
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> {
            DictionaryView dictionary = dictionaries.lockDictionary(code)
                    .orElseThrow(() -> notFound(code));
            requireDraft(code, version);
            List<DictionaryNodeView> nodes = dictionaries.nodes(code, version);
            // 重新推一遍树：落库的是上一次导入的结论，发布是最后一道闸门，不复用它的结论。
            List<DictionaryNodeView> resolved = DictionaryTree.resolve(
                    nodes.stream().map(node -> new DictionaryNodeDraft(node.code(), node.parentCode(),
                            node.label())).toList(),
                    dictionary.maxDepth());
            String digest = DictionaryDigest.of(resolved);
            dictionaries.markPublished(code, version, digest, resolved.size());
            dictionaries.setCurrentVersion(code, version);
            audit.record(operator, traceId, DictionaryAudit.PUBLISH, code,
                    "version=" + version + " digest=" + digest + " nodes=" + resolved.size());
            return requiredVersion(code, version);
        });
    }

    public List<DictionaryView> list() {
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, dictionaries::allDictionaries);
    }

    public DictionaryView get(String code) {
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> required(code));
    }

    public List<DictionaryVersionView> versions(String code) {
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> {
            required(code);
            return dictionaries.versionsOf(code);
        });
    }

    public DictionaryVersionView version(String code, String version) {
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> requiredVersion(code, version));
    }

    /** 运营视角：连草稿的节点也看得到。 */
    public List<DictionaryNodeView> nodes(String code, String version) {
        return tenantScope.call(DictionaryAudit.CONTROL_PLANE, () -> {
            requiredVersion(code, version);
            return dictionaries.nodes(code, version);
        });
    }

    // ---------------------------------------------------------------- 校验

    private DictionaryView required(String code) {
        return dictionaries.findDictionary(code).orElseThrow(() -> notFound(code));
    }

    private DictionaryVersionView requiredVersion(String code, String version) {
        return dictionaries.findVersion(code, version).orElseThrow(() ->
                new DictionaryNotFoundException("dictionary version not found: " + code + "/" + version));
    }

    private void requireDraft(String code, String version) {
        DictionaryVersionView found = requiredVersion(code, version);
        if (found.status() != DictionaryVersionStatus.DRAFT) {
            throw new DictionaryConflictException("dictionary_version_published",
                    "版本 " + version + " 已发布，不能再改；请新建一版");
        }
    }

    private static DictionaryNotFoundException notFound(String code) {
        return new DictionaryNotFoundException("dictionary not found: " + code);
    }

    private static String requireCode(String code) {
        if (code == null || !DictionaryLimits.CODE.matcher(code).matches()) {
            throw new InvalidDictionaryRequestException("字典代码必须是小写字母开头的 2–64 位字母数字或连字符");
        }
        return code;
    }

    private static String requireVersion(String version) {
        if (version == null || !DictionaryLimits.VERSION.matcher(version).matches()) {
            throw new InvalidDictionaryRequestException(
                    "版本名必须以字母或数字开头，只含字母数字与 . _ -，最长 32 位");
        }
        return version;
    }

    private static String requireName(String name) {
        String clean = name == null ? "" : name.trim();
        if (clean.isEmpty() || clean.length() > DictionaryLimits.MAX_NAME) {
            throw new InvalidDictionaryRequestException("name 必须是 1–" + DictionaryLimits.MAX_NAME + " 个字符");
        }
        return clean;
    }

    private static List<DictionaryNodeDraft> requireNodes(List<DictionaryNodeDraft> nodes) {
        if (nodes == null || nodes.isEmpty()) {
            throw new InvalidDictionaryRequestException("nodes 不能为空");
        }
        if (nodes.size() > DictionaryLimits.MAX_NODES) {
            throw new InvalidDictionaryRequestException(
                    "一版字典最多 " + DictionaryLimits.MAX_NODES + " 个节点，收到 " + nodes.size());
        }
        return nodes.stream().map(DictionaryService::requireNode).toList();
    }

    private static DictionaryNodeDraft requireNode(DictionaryNodeDraft node) {
        if (node == null || node.code() == null || !DictionaryLimits.NODE_CODE.matcher(node.code()).matches()) {
            throw new InvalidDictionaryRequestException(
                    "节点代码必须以字母或数字开头，只含字母数字与 _ -，最长 32 位");
        }
        String parent = node.parentCode() == null || node.parentCode().isBlank() ? null : node.parentCode();
        if (parent != null && !DictionaryLimits.NODE_CODE.matcher(parent).matches()) {
            throw new InvalidDictionaryRequestException("节点 " + node.code() + " 的父代码不合法");
        }
        String label = node.label() == null ? "" : node.label().trim();
        if (label.isEmpty() || label.length() > DictionaryLimits.MAX_LABEL) {
            throw new InvalidDictionaryRequestException(
                    "节点 " + node.code() + " 的标签必须是 1–" + DictionaryLimits.MAX_LABEL + " 个字符");
        }
        return new DictionaryNodeDraft(node.code(), parent, label);
    }
}
