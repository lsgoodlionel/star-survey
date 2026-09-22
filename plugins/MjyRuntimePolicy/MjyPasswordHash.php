<?php

/**
 * 访问密码校验（ADR 0016 决定 5）。
 *
 * 格式 `pbkdf2-sha256$<迭代次数>$<salt base64>$<hash base64>`，由平台生成；插件只校验，
 * 从不生成、从不记录。比较用 hash_equals，耗时与输入无关。
 */
class MjyPasswordHash
{
    private const SCHEME = 'pbkdf2-sha256';
    private const MIN_ITERATIONS = 100000;
    private const MAX_ITERATIONS = 10000000;
    private const HASH_BYTES = 32;

    public static function verify(string $password, string $encoded): bool
    {
        $parts = explode('$', $encoded);
        if (count($parts) !== 4 || $parts[0] !== self::SCHEME || !ctype_digit($parts[1])) {
            return false;
        }
        $iterations = (int) $parts[1];
        $salt = base64_decode($parts[2], true);
        $expected = base64_decode($parts[3], true);
        if ($iterations < self::MIN_ITERATIONS || $iterations > self::MAX_ITERATIONS
            || $salt === false || $expected === false || strlen($expected) !== self::HASH_BYTES) {
            return false;
        }
        $actual = hash_pbkdf2('sha256', $password, $salt, $iterations, self::HASH_BYTES, true);
        return hash_equals($expected, $actual);
    }
}
