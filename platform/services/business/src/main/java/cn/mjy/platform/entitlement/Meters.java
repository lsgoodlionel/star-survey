package cn.mjy.platform.entitlement;

/** 已登记的计量代码（与 meter_definition 表的种子数据一致）。 */
public final class Meters {

    /** 有效完成答卷：计费唯一口径（P-03），按问卷累计、永不归零。 */
    public static final String VALID_COMPLETED_RESPONSE = "response.valid_completed";

    /** AI 消耗的模型 token，按订阅期累计；入账时带能力与模型维度，供版本化价目表定价（U-02）。 */
    public static final String AI_TOKENS = "ai.tokens";

    /** 成员席位上限，按租户、不归零；占用数由权限模块统计（见 EntitlementSeatAllowance）。 */
    public static final String MEMBER_SEATS = "member.seats";

    private Meters() {
    }
}
