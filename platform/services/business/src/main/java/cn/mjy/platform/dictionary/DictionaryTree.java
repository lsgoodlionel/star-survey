package cn.mjy.platform.dictionary;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 把一份「代码 / 父代码 / 标签」的平表算成一棵树：定深度、定叶子、把不成立的形状挡在发布之前。
 *
 * <p>深度不是导入方声明的，是从根往下推出来的。这条选择顺带解决了环：环里的节点谁也推不出深度，
 * 于是「推不出深度的节点」这一条检查同时覆盖了孤儿与环，不必另写一套访问标记。
 */
final class DictionaryTree {

    private DictionaryTree() {
    }

    /**
     * @return 按导入顺序排列、补齐了深度与叶子标记的节点
     * @throws InvalidDictionaryRequestException 有孤儿、有环、超过本字典允许的深度
     */
    static List<DictionaryNodeView> resolve(List<DictionaryNodeDraft> drafts, int maxDepth) {
        if (drafts.isEmpty()) {
            throw new InvalidDictionaryRequestException("一版字典至少要有一个节点");
        }
        Map<String, DictionaryNodeDraft> byCode = index(drafts);
        Set<String> parents = parentsOf(drafts);
        Map<String, Integer> depths = depths(drafts, byCode, maxDepth);

        List<DictionaryNodeView> resolved = new ArrayList<>(drafts.size());
        for (DictionaryNodeDraft draft : drafts) {
            resolved.add(new DictionaryNodeView(draft.code(), draft.parentCode(), depths.get(draft.code()),
                    draft.label(), !parents.contains(draft.code())));
        }
        return resolved;
    }

    /** 代码在一版之内全局唯一：重复的代码会让「一条路径只有一种解释」不再成立。 */
    private static Map<String, DictionaryNodeDraft> index(List<DictionaryNodeDraft> drafts) {
        Map<String, DictionaryNodeDraft> byCode = new LinkedHashMap<>();
        for (DictionaryNodeDraft draft : drafts) {
            if (byCode.putIfAbsent(draft.code(), draft) != null) {
                throw new InvalidDictionaryRequestException("节点代码重复：" + draft.code());
            }
        }
        return byCode;
    }

    private static Set<String> parentsOf(List<DictionaryNodeDraft> drafts) {
        Set<String> parents = new HashSet<>();
        for (DictionaryNodeDraft draft : drafts) {
            if (draft.parentCode() != null) {
                parents.add(draft.parentCode());
            }
        }
        return parents;
    }

    /** 逐个节点沿父链往上走到根，路上记下深度；走不到根（孤儿、环、太深）就拒绝整份导入。 */
    private static Map<String, Integer> depths(List<DictionaryNodeDraft> drafts,
            Map<String, DictionaryNodeDraft> byCode, int maxDepth) {
        Map<String, Integer> depths = new HashMap<>();
        for (DictionaryNodeDraft draft : drafts) {
            if (depths.containsKey(draft.code())) {
                continue;
            }
            resolveDepth(draft, byCode, depths, maxDepth);
        }
        return depths;
    }

    private static int resolveDepth(DictionaryNodeDraft node, Map<String, DictionaryNodeDraft> byCode,
            Map<String, Integer> depths, int maxDepth) {
        List<DictionaryNodeDraft> chain = new ArrayList<>();
        DictionaryNodeDraft cursor = node;
        // 走到已知深度的节点或根为止；步数超过节点总数说明在打转。
        while (cursor != null && !depths.containsKey(cursor.code())) {
            if (chain.size() > byCode.size()) {
                throw new InvalidDictionaryRequestException("节点 " + node.code() + " 的父链成环，推不出层级");
            }
            chain.add(cursor);
            cursor = parentOf(cursor, byCode);
        }
        int depth = cursor == null ? 0 : depths.get(cursor.code());
        for (int i = chain.size() - 1; i >= 0; i--) {
            depth++;
            if (depth > maxDepth) {
                throw new InvalidDictionaryRequestException(
                        "节点 " + chain.get(i).code() + " 落在第 " + depth + " 层，超过本字典的 " + maxDepth + " 层");
            }
            depths.put(chain.get(i).code(), depth);
        }
        return depth;
    }

    private static DictionaryNodeDraft parentOf(DictionaryNodeDraft node, Map<String, DictionaryNodeDraft> byCode) {
        if (node.parentCode() == null) {
            return null;
        }
        if (node.parentCode().equals(node.code())) {
            throw new InvalidDictionaryRequestException("节点 " + node.code() + " 的父节点是它自己");
        }
        DictionaryNodeDraft parent = byCode.get(node.parentCode());
        if (parent == null) {
            throw new InvalidDictionaryRequestException(
                    "节点 " + node.code() + " 的父节点 " + node.parentCode() + " 不在这一版里");
        }
        return parent;
    }
}
