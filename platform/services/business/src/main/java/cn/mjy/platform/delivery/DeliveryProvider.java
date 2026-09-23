package cn.mjy.platform.delivery;

/**
 * 渠道商适配器（邮件或短信）。接入真实服务商时实现本接口并注册为 bean 即可，其余代码不动。
 *
 * <p><b>幂等是接口契约的一部分</b>：{@link DeliveryMessage#idempotencyKey()} 由平台按 (任务, 收件人) 生成，
 * 实现必须把它原样交给服务商的幂等机制。平台的认领与实际发送分处两个事务，进程在中间死掉时会用同一个
 * 幂等键重发——只有服务商按键去重，才能保证"崩溃后恢复不重复投递"。服务商不支持幂等键时，
 * 实现应自己在本地保存"该键已投递"的记录，并在报告里写明其可靠性边界。
 *
 * <p>实现不得记录收件地址（邮箱、手机号）：那是租户联系数据。需要写日志时用 {@link Redaction}。
 */
public interface DeliveryProvider {

    DeliveryChannel channel();

    /** 渠道商标识，写进发送台账与回执（回执按它匹配）。只允许 1–64 个小写字母、数字与连字符。 */
    String name();

    SendResult send(DeliveryMessage message);
}
