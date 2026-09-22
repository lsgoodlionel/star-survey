<?php

/**
 * 离线 CSV 地区数据源：每行 `起始IP,结束IP,地区码`（可带引号），IPv4 与 IPv6 均可。
 *
 * 形状与 DB-IP Lite（CC BY 4.0）、IP2Location LITE（CC BY-SA 4.0）的国家级 CSV 导出一致；
 * 用哪一份、如何署名由交付方按许可决定（ADR 0016 决定 8）。路径来自环境变量 MJY_GEOIP_CSV。
 *
 * 逐行扫描、命中即停：原型够用，但对几十万行的全量库每次请求都要扫一遍——
 * 生产应换成预排序后二分查找或 APCu 缓存（已列为缺口）。
 */
class MjyCsvRegionResolver implements MjyRegionResolver
{
    private const ENV = 'MJY_GEOIP_CSV';

    /** @var string */
    private $path;

    public function __construct(string $path)
    {
        $this->path = $path;
    }

    public static function fromEnvironment(): MjyRegionResolver
    {
        $path = getenv(self::ENV);
        return ($path === false || $path === '') ? new MjyNullRegionResolver() : new self($path);
    }

    public function resolve(string $ip): ?string
    {
        if (!MjyIpRules::isValidIp($ip) || !is_readable($this->path)) {
            return null;
        }
        $address = inet_pton($ip);
        $handle = fopen($this->path, 'rb');
        if ($handle === false) {
            return null;
        }
        try {
            while (($row = fgetcsv($handle, 0, ',', '"', '\\')) !== false) {
                $region = $this->match($row, $address);
                if ($region !== null) {
                    return $region;
                }
            }
            return null;
        } finally {
            fclose($handle);
        }
    }

    private function match(array $row, string $address): ?string
    {
        if (count($row) < 3 || !MjyIpRules::isValidIp(trim((string) $row[0])) || !MjyIpRules::isValidIp(trim((string) $row[1]))) {
            return null;
        }
        $start = inet_pton(trim((string) $row[0]));
        $end = inet_pton(trim((string) $row[1]));
        if (strlen($start) !== strlen($address) || strlen($end) !== strlen($address)) {
            return null;
        }
        if (strcmp($address, $start) < 0 || strcmp($address, $end) > 0) {
            return null;
        }
        $region = strtoupper(trim((string) $row[2]));
        return $region === '' ? null : $region;
    }
}
