package cn.mjy.platform.access;

/** 判定原因，供调用方给出可理解的提示与审计。 */
public enum DecisionReason {
    /** 有授权覆盖该动作。 */
    GRANTED,
    /** 资源不存在或不属于当前租户（行级安全下两者不可区分，也不应区分）。 */
    UNKNOWN_RESOURCE,
    /** 不是本租户的在职成员（未加入、邀请未接受或已移除）。 */
    NOT_AN_ACTIVE_MEMBER,
    /** 没有任何生效的授权包含该权限。 */
    NO_MATCHING_GRANT,
    /** 租户开启了发布审核，而该成员没有免审权限，须走审核。 */
    APPROVAL_REQUIRED
}
