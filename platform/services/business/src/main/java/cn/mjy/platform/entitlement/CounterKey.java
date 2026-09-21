package cn.mjy.platform.entitlement;

/**
 * 余额的累计单位：计量 x 对象 x 周期。
 * 对象：按租户累计的计量为空串，按问卷累计的为问卷标识；
 * 周期：永不归零的计量为 {@code lifetime}，按订阅期归零的为订阅期 id。
 */
record CounterKey(String meter, String subject, String periodKey) {

    static final String TENANT_SUBJECT = "";
    static final String LIFETIME = "lifetime";

    static CounterKey of(MeterDefinition meter, String requestedSubject, Subscription subscription) {
        String subject = switch (meter.scope()) {
            case TENANT -> TENANT_SUBJECT;
            case SURVEY -> requireSubject(meter, requestedSubject);
        };
        String period = switch (meter.resetPolicy()) {
            case NEVER -> LIFETIME;
            case SUBSCRIPTION_PERIOD -> subscription.id().toString();
        };
        return new CounterKey(meter.code(), subject, period);
    }

    private static String requireSubject(MeterDefinition meter, String subject) {
        if (subject == null || subject.isBlank() || subject.length() > 200) {
            throw new IllegalArgumentException("meter " + meter.code() + " is counted per survey: a survey subject is required");
        }
        return subject;
    }
}
