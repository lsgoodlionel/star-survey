package cn.mjy.platform.engine;

import java.util.Set;

/**
 * 批次里有事件自称属于另一个实例（与验签确认的实例不同）。整批拒收、一行不写：
 * 否则持有实例 X 密钥的一方就能用 X 的签名替实例 Y（可能属于别的租户）写事件。
 */
public class EngineInstanceMismatchException extends RuntimeException {

    private final String authenticated;
    private final Set<String> claimed;

    public EngineInstanceMismatchException(String authenticated, Set<String> claimed) {
        super("batch signed by engine instance " + authenticated + " carries events of " + claimed);
        this.authenticated = authenticated;
        this.claimed = Set.copyOf(claimed);
    }

    public String authenticated() {
        return authenticated;
    }

    public Set<String> claimed() {
        return claimed;
    }
}
