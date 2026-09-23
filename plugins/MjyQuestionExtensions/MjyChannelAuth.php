<?php

/**
 * 网关↔插件鉴权通道的验签（ADR 0018，契约 platform/contracts/plugin-channel-v1.md）。
 *
 * 这一层**没有任何 IO**：密钥从构造参数（调用方读环境变量）派生，验签只做进程内的
 * HMAC。这是「未验签的请求零数据库、零解密」的前提——未签名洪水的单请求成本因此
 * 只是一次哈希，也正因为便宜，限流才放到验签**之后**（决定 6）。
 *
 * 密钥派生：
 *
 *     通道密钥 = hex(HMAC-SHA256(实例密钥, "mjy-plugin-channel/v1/" + 实例标识))
 *
 * 实例密钥就是引擎已配好的 MJY_PLATFORM_EVENTS_SECRET（ADR 0003），所以引擎侧
 * 不需要新增环境变量。派生是单向的：拿到通道密钥推不出实例密钥，
 * **网关被攻破读得到副表答案，但伪造不了引擎事件**。
 *
 * 签名：
 *
 *     签名串 = "<ts>" + "." + <规范化查询串>
 *     sig    = hex(HMAC-SHA256(通道密钥, 签名串))
 *
 * 规范化查询串覆盖除 sig 外的**全部**参数，所以签好的请求改投不到别的插件函数，
 * 也改投不到别人的答卷。
 *
 * check() 返回的是**给日志用的稳定原因码**。调用方必须把所有非空返回值归到同一个
 * 出口（统一 401），绝不能按原因分叉——那就是预言机（ADR 0014 飞书回调那次）。
 */
class MjyChannelAuth
{
    /** 派生上下文；将来换算法时升版本号，新旧密钥互不相同。 */
    public const DERIVATION_PREFIX = 'mjy-plugin-channel/v1/';
    public const MAX_SKEW_SECONDS = 300;
    private const ALGORITHM = 'sha256';
    private const MIN_INSTANCE_SECRET_BYTES = 32;
    private const SIGNATURE_PARAM = 'sig';
    private const TIMESTAMP_PARAM = 'ts';
    private const SIGNATURE_PATTERN = '/\A[0-9a-fA-F]{64}\z/D';
    private const TIMESTAMP_PATTERN = '/\A[0-9]{1,12}\z/D';

    /** @var string[] 可接受的通道密钥：当前 ＋ 轮换期的上一代，最多两个 */
    private $channelSecrets = [];

    /**
     * @param string[] $instanceSecrets 实例密钥（当前，以及轮换期的上一代）。
     *                                  不足 32 字节或为空的一律忽略——配了半截等于没配，
     *                                  不得因此放行（失败即关闭）。
     */
    public function __construct(array $instanceSecrets, string $engineInstanceId)
    {
        foreach ($instanceSecrets as $instanceSecret) {
            if (self::isUsable((string) $instanceSecret)) {
                $this->channelSecrets[] = self::deriveChannelSecret((string) $instanceSecret, $engineInstanceId);
            }
        }
    }

    private static function isUsable(string $instanceSecret): bool
    {
        return strlen($instanceSecret) >= self::MIN_INSTANCE_SECRET_BYTES;
    }

    /**
     * 通道密钥（小写十六进制）。调用方负责保密：不得写日志。
     */
    public static function deriveChannelSecret(string $instanceSecret, string $engineInstanceId): string
    {
        return hash_hmac(self::ALGORITHM, self::DERIVATION_PREFIX . $engineInstanceId, $instanceSecret);
    }

    /**
     * 规范化查询串：除 sig 外的全部参数，按参数名升序，键值各自 RFC 3986 编码，`&` 连接。
     *
     * PHP 的 rawurlencode 与 Python 的 quote(safe="") 对 RFC 3986 的定义一致，
     * 两端因此算得出同一个串（两侧各有一组同一份固定向量守着这件事）。
     *
     * @param array<string, string> $params
     */
    public static function canonicalQuery(array $params): string
    {
        unset($params[self::SIGNATURE_PARAM]);
        ksort($params, SORT_STRING);
        $pairs = [];
        foreach ($params as $key => $value) {
            $pairs[] = rawurlencode((string) $key) . '=' . rawurlencode((string) $value);
        }
        return implode('&', $pairs);
    }

    /**
     * @param array<string, string> $params 查询参数，含 sig
     * @return string 空串＝通过；否则是给日志用的稳定原因码（绝不进响应体）
     */
    public function check(array $params, int $now): string
    {
        if ($this->channelSecrets === []) {
            return 'no_channel_secret';
        }
        $signature = isset($params[self::SIGNATURE_PARAM]) ? (string) $params[self::SIGNATURE_PARAM] : '';
        if ($signature === '') {
            return 'missing_signature';
        }
        if (preg_match(self::SIGNATURE_PATTERN, $signature) !== 1) {
            return 'bad_signature_format';
        }
        $timestamp = isset($params[self::TIMESTAMP_PARAM]) ? (string) $params[self::TIMESTAMP_PARAM] : '';
        if ($timestamp === '') {
            return 'missing_timestamp';
        }
        if (preg_match(self::TIMESTAMP_PATTERN, $timestamp) !== 1) {
            return 'bad_timestamp';
        }
        if (abs((int) $timestamp - $now) > self::MAX_SKEW_SECONDS) {
            return 'stale_timestamp';
        }
        return $this->matches($timestamp, self::canonicalQuery($params), $signature)
            ? '' : 'bad_signature';
    }

    /**
     * 逐个密钥比完，不短路：轮换期有两个密钥有效，比较耗时不得随「是哪一个对上的」变化。
     * hash_equals 的耗时与内容无关；`hash_equals(...) || $matched` 的左操作数总会求值。
     */
    private function matches(string $timestamp, string $canonical, string $signature): bool
    {
        $message = $timestamp . '.' . $canonical;
        $matched = false;
        foreach ($this->channelSecrets as $channelSecret) {
            $expected = hash_hmac(self::ALGORITHM, $message, $channelSecret);
            $matched = hash_equals($expected, strtolower($signature)) || $matched;
        }
        return $matched;
    }
}
