package cn.mjy.platform.access;

import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * 某条授权为某资源带来的一项权限。depth 为授权所在节点到目标资源的层数（0 为资源本身），
 * 整租户授权的 depth 为空。
 */
record GrantMatch(Permission permission, String roleCode, UUID grantResourceId, Integer depth) {

    /** 确定性的"最近优先"：离资源越近越优先，整租户授权最后，同层按角色码排序。 */
    static final Comparator<GrantMatch> CLOSEST_FIRST = Comparator
            .comparing((GrantMatch m) -> m.depth() == null ? Integer.MAX_VALUE : m.depth())
            .thenComparing(GrantMatch::roleCode);

    static Optional<GrantMatch> closest(List<GrantMatch> matches, Permission permission) {
        return matches.stream().filter(m -> m.permission() == permission).min(CLOSEST_FIRST);
    }

    String describe() {
        String scope = grantResourceId == null ? "tenant" : grantResourceId.toString();
        return "role " + roleCode + " on " + scope;
    }
}
