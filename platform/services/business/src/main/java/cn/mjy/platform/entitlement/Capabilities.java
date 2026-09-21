package cn.mjy.platform.entitlement;

/** 已登记的能力代码（与 capability_definition 表的种子数据一致）。套餐只负责开关它们。 */
public final class Capabilities {

    public static final String SURVEY_READ = "survey.read";
    public static final String SURVEY_WRITE = "survey.write";
    public static final String RESPONSE_COLLECT = "response.collect";
    public static final String RESPONSE_EXPORT = "response.export";
    public static final String AI_SURVEY_GENERATE = "ai.survey_generate";
    public static final String AI_RESPONSE_ANALYZE = "ai.response_analyze";

    private Capabilities() {
    }
}
