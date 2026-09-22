<?php

/**
 * 客户端 IP 与网段规则（ADR 0016 决定 7）。
 *
 * 不用引擎的 getIPAddress()：它先看 Client-IP、再看 X-Forwarded-For
 * （common_helper.php:5287），任何客户端都能伪造。这里默认只信 REMOTE_ADDR；
 * 只有 REMOTE_ADDR 属于显式声明的可信代理时，才从 X-Forwarded-For 右往左
 * 取第一个不可信的地址。
 */
class MjyIpRules
{
    /**
     * @param array<string, mixed> $server 通常是 $_SERVER
     * @param string[] $trustedProxies CIDR 列表
     */
    public static function clientIp(array $server, array $trustedProxies): string
    {
        $remote = (string) ($server['REMOTE_ADDR'] ?? '');
        if (!self::isValidIp($remote) || !self::matchesAny($remote, $trustedProxies)) {
            return $remote;
        }
        $chain = array_map('trim', explode(',', (string) ($server['HTTP_X_FORWARDED_FOR'] ?? '')));
        $client = $remote;
        foreach (array_reverse($chain) as $hop) {
            if (!self::isValidIp($hop)) {
                // 链条断了：再往左都是不可验证的，停在最后一个可信跳上。
                break;
            }
            $client = $hop;
            if (!self::matchesAny($hop, $trustedProxies)) {
                break;
            }
        }
        return $client;
    }

    /**
     * 可信代理，来自环境变量 MJY_TRUSTED_PROXIES（逗号分隔的 CIDR），非法项忽略。
     *
     * @return string[]
     */
    public static function trustedProxiesFromEnvironment(): array
    {
        $configured = getenv('MJY_TRUSTED_PROXIES');
        if ($configured === false || trim($configured) === '') {
            return [];
        }
        return array_values(array_filter(array_map('trim', explode(',', $configured)), [self::class, 'isValidCidr']));
    }

    /**
     * @param string[] $cidrs
     */
    public static function matchesAny(string $ip, array $cidrs): bool
    {
        foreach ($cidrs as $cidr) {
            if (self::matches($ip, $cidr)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 同族才比较：IPv4 地址永远不匹配 IPv6 网段，反之亦然（含 ::ffff:a.b.c.d 映射地址）。
     */
    public static function matches(string $ip, string $cidr): bool
    {
        $network = self::parseCidr($cidr);
        $address = self::isValidIp($ip) ? inet_pton($ip) : false;
        if ($network === null || $address === false || strlen($address) !== strlen($network[0])) {
            return false;
        }
        [$base, $prefix] = $network;
        $fullBytes = intdiv($prefix, 8);
        if (strncmp($address, $base, $fullBytes) !== 0) {
            return false;
        }
        $remainingBits = $prefix % 8;
        if ($remainingBits === 0) {
            return true;
        }
        $mask = (0xff << (8 - $remainingBits)) & 0xff;
        return (ord($address[$fullBytes]) & $mask) === (ord($base[$fullBytes]) & $mask);
    }

    public static function isValidCidr(string $cidr): bool
    {
        return self::parseCidr($cidr) !== null;
    }

    public static function isValidIp(string $ip): bool
    {
        return filter_var($ip, FILTER_VALIDATE_IP) !== false;
    }

    /**
     * 地区规则 "CN" 覆盖 "CN-BJ"；"CN-BJ" 只匹配自己。
     */
    public static function regionMatches(string $region, string $rule): bool
    {
        return $region === $rule || (strlen($rule) === 2 && strpos($region, $rule . '-') === 0);
    }

    /**
     * @return array{0: string, 1: int}|null 网段起始地址的二进制形式与前缀长度
     */
    private static function parseCidr(string $cidr): ?array
    {
        $parts = explode('/', trim($cidr), 2);
        if (!self::isValidIp($parts[0])) {
            return null;
        }
        $binary = inet_pton($parts[0]);
        $maxPrefix = strlen($binary) * 8;
        if (!isset($parts[1])) {
            return [$binary, $maxPrefix];
        }
        if (!ctype_digit($parts[1]) || (int) $parts[1] > $maxPrefix) {
            return null;
        }
        return [$binary, (int) $parts[1]];
    }
}
