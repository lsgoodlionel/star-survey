package cn.mjy.platform.entitlement;

/** 能力登记：代码、访问性质，以及使用时消耗的计量（不计量则为 null）。 */
public record CapabilityDefinition(String code, AccessKind access, String meter) {

    public boolean isMetered() {
        return meter != null;
    }
}
