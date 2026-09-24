<?php

/**
 * 离线 CSV 地区数据源：每行 `起始IP,结束IP,地区码`（可带引号），IPv4 与 IPv6 均可。
 *
 * 形状与 DB-IP Lite（CC BY 4.0）、IP2Location LITE（CC BY-SA 4.0）的国家级 CSV 导出一致；
 * 用哪一份、如何署名由交付方按许可决定（ADR 0016 决定 8）。路径来自环境变量 MJY_GEOIP_CSV。
 *
 * **查询走预排序索引＋二分**（MjyGeoIpIndex），不再逐行扫描：全量库有几十万行，
 * 逐行扫描意味着每个作答请求都要把它读一遍。索引在首次查询时按 CSV 建出来，
 * CSV 变新了自动重建；建索引是 O(n log n) 一次，之后每次查询是 O(log n) 次 fseek。
 *
 * 索引所在目录不可写（只读挂载的数据盘）时退回逐行扫描：答案照旧，只是慢——
 * 这条退路刻意保留，因为把「数据盘只读」变成「所有人都定位不到、按 regionUnknown 全拒」
 * 是个比慢得多的故障。可用 MJY_GEOIP_INDEX 指向一个可写路径来避开它。
 */
class MjyCsvRegionResolver implements MjyRegionResolver
{
    private const ENV = 'MJY_GEOIP_CSV';
    private const INDEX_ENV = 'MJY_GEOIP_INDEX';

    /** @var string */
    private $path;

    /** @var string */
    private $indexPath;

    public function __construct(string $path, ?string $indexPath = null)
    {
        $this->path = $path;
        $this->indexPath = $indexPath ?? MjyGeoIpIndex::pathFor($path);
    }

    public static function fromEnvironment(): MjyRegionResolver
    {
        $path = getenv(self::ENV);
        if ($path === false || $path === '') {
            return new MjyNullRegionResolver();
        }
        $index = getenv(self::INDEX_ENV);
        return new self($path, ($index === false || $index === '') ? null : $index);
    }

    public function resolve(string $ip): ?string
    {
        if (!MjyIpRules::isValidIp($ip)) {
            return null;
        }
        $address = inet_pton($ip);
        if ($address === false) {
            return null;
        }
        if (MjyGeoIpIndex::ensure($this->path, $this->indexPath)) {
            return MjyGeoIpIndex::lookup($this->indexPath, $address);
        }
        return $this->scan($address);
    }

    /**
     * 退化路径：索引建不出来时逐行扫描。与索引的语义差别只有一处——范围重叠时这里返回
     * 文件里靠前的那一条（见 MjyGeoIpIndex 的类注释）。
     */
    private function scan(string $address): ?string
    {
        if (!is_readable($this->path)) {
            return null;
        }
        $handle = @fopen($this->path, 'rb');
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
