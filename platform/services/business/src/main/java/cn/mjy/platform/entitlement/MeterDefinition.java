package cn.mjy.platform.entitlement;

/** 计量登记：代码、单位、累计范围与归零策略。 */
public record MeterDefinition(String code, String unit, MeterScope scope, ResetPolicy resetPolicy) {
}
