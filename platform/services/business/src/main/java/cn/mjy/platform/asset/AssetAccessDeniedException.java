package cn.mjy.platform.asset;

import cn.mjy.platform.access.DecisionReason;

/** 权限不足：原因码直接沿用 access 模块的判定结果，不另造一套。 */
public class AssetAccessDeniedException extends RuntimeException {

    private final transient DecisionReason reason;

    public AssetAccessDeniedException(DecisionReason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public DecisionReason reason() {
        return reason;
    }
}
