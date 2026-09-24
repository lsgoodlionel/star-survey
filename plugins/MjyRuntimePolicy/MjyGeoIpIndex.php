<?php

/**
 * 离线地区库的**预排序索引**：把 `起始IP,结束IP,地区码` 的 CSV 排好序写成定宽记录，
 * 之后每次查询二分查找，不再逐行扫描（ADR 0016 缺口「离线 CSV 地区数据源逐行扫描」的收口）。
 *
 * 为什么要另起一个文件而不是把 CSV 读进内存排序：全量库有几十万行，按每行一个 PHP 数组算是几十 MB，
 * 而 PHP 的进程模型下这份内存每个 worker 各占一份、每次冷启动各建一次。定宽文件加 fseek 则是
 * O(log n) 次 seek、常数级内存，与库有多大无关。
 *
 * 文件格式（大端）：
 *
 *   魔数 "MJYGEO1\n"          8 字节
 *   IPv4 条数                 uint32
 *   IPv6 条数                 uint32
 *   IPv4 区段                 每条 4+4+8 = 16 字节（起始、结束、地区码右侧补 NUL）
 *   IPv6 区段                 每条 16+16+8 = 40 字节
 *
 * 两个地址族分段存放，因为定宽查找要求同一段里每条记录一样长；也顺带保证 IPv4 的地址
 * 不可能在 IPv6 段里命中（`strcmp` 比的是字节，不分家族）。
 *
 * **与逐行扫描的一处语义差别**：范围重叠时，逐行扫描返回文件里靠前的那一条，二分返回
 * 起始地址最大的那一条（即更具体的那一条）。地区库的范围本就不该重叠；真重叠时后者更讲得通。
 */
class MjyGeoIpIndex
{
    private const MAGIC = "MJYGEO1\n";
    private const HEADER_LENGTH = 16;
    /** 地区码最长几个字节：ISO 3166-2 最长形如 `CN-BJ`，8 字节留足余量。 */
    private const REGION_WIDTH = 8;

    /** 索引放在 CSV 旁边。CSV 所在目录不可写时由调用方另给一个路径。 */
    public static function pathFor(string $csvPath): string
    {
        return $csvPath . '.mjyidx';
    }

    /**
     * 索引不存在或比 CSV 旧就重建。
     *
     * @return bool 索引现在可用
     */
    public static function ensure(string $csvPath, string $indexPath): bool
    {
        if (self::isFresh($csvPath, $indexPath)) {
            return true;
        }
        return self::build($csvPath, $indexPath);
    }

    /** 索引存在、格式对得上、且不比 CSV 旧。 */
    private static function isFresh(string $csvPath, string $indexPath): bool
    {
        if (!is_readable($indexPath)) {
            return false;
        }
        $csvTime = @filemtime($csvPath);
        $indexTime = @filemtime($indexPath);
        if ($csvTime !== false && $indexTime !== false && $indexTime < $csvTime) {
            return false;
        }
        // 截断或写坏的索引要当作不存在：宁可重建一次，也不能拿半个文件去二分。
        return self::header($indexPath) !== null;
    }

    /**
     * 读 CSV、排序、原子落盘。先写临时文件再 rename，所以并发重建不会让别的进程读到半个文件。
     *
     * @return bool 建成了没有
     */
    public static function build(string $csvPath, string $indexPath): bool
    {
        $rows = self::readRows($csvPath);
        if ($rows === null) {
            return false;
        }
        // 各自按起始地址的字节序排序；同一族里定宽，二分才成立。
        sort($rows[4], SORT_STRING);
        sort($rows[16], SORT_STRING);

        $body = self::MAGIC . pack('N', count($rows[4])) . pack('N', count($rows[16]))
            . implode('', $rows[4]) . implode('', $rows[16]);

        $temporary = $indexPath . '.' . getmypid() . '.tmp';
        if (@file_put_contents($temporary, $body, LOCK_EX) === false) {
            return false;
        }
        if (!@rename($temporary, $indexPath)) {
            @unlink($temporary);
            return false;
        }
        return true;
    }

    /**
     * 把 CSV 读成两组定宽记录（键是地址字节数：4 或 16）。坏行跳过，不让它变成一条可命中的范围。
     *
     * @return array<int, string[]>|null 读不了返回 null
     */
    private static function readRows(string $csvPath)
    {
        $handle = @fopen($csvPath, 'rb');
        if ($handle === false) {
            return null;
        }
        $rows = [4 => [], 16 => []];
        try {
            while (($row = fgetcsv($handle, 0, ',', '"', '\\')) !== false) {
                $record = self::record($row);
                if ($record !== null) {
                    $rows[strlen($record) === 16 ? 4 : 16][] = $record;
                }
            }
        } finally {
            fclose($handle);
        }
        return $rows;
    }

    /** 一行 → 一条定宽记录（起始 ＋ 结束 ＋ 补齐的地区码）；这一行不成立时返回 null。 */
    private static function record(array $row): ?string
    {
        if (count($row) < 3) {
            return null;
        }
        $start = self::address((string) $row[0]);
        $end = self::address((string) $row[1]);
        if ($start === null || $end === null || strlen($start) !== strlen($end)) {
            return null;
        }
        if (strcmp($start, $end) > 0) {
            return null;
        }
        $region = strtoupper(trim((string) $row[2]));
        if ($region === '' || strlen($region) > self::REGION_WIDTH) {
            return null;
        }
        return $start . $end . str_pad($region, self::REGION_WIDTH, "\0");
    }

    private static function address(string $raw): ?string
    {
        $value = trim($raw);
        if (!MjyIpRules::isValidIp($value)) {
            return null;
        }
        $packed = inet_pton($value);
        return $packed === false ? null : $packed;
    }

    /**
     * 二分查找：找出起始地址不超过 $address 的最后一条，再看它的结束地址够不够得着。
     *
     * @param string $address inet_pton 之后的地址字节
     */
    public static function lookup(string $indexPath, string $address): ?string
    {
        $header = self::header($indexPath);
        if ($header === null) {
            return null;
        }
        $width = strlen($address);
        if ($width !== 4 && $width !== 16) {
            return null;
        }
        [$count, $offset] = self::section($header, $width);
        if ($count === 0) {
            return null;
        }
        $handle = @fopen($indexPath, 'rb');
        if ($handle === false) {
            return null;
        }
        try {
            return self::search($handle, $address, $width, $count, $offset);
        } finally {
            fclose($handle);
        }
    }

    /** @return array{0:int,1:int} 这个地址族的条数与区段起点 */
    private static function section(array $header, int $width): array
    {
        [$v4, $v6] = $header;
        if ($width === 4) {
            return [$v4, self::HEADER_LENGTH];
        }
        return [$v6, self::HEADER_LENGTH + $v4 * (4 + 4 + self::REGION_WIDTH)];
    }

    /** @param resource $handle */
    private static function search($handle, string $address, int $width, int $count, int $offset): ?string
    {
        $recordLength = $width + $width + self::REGION_WIDTH;
        $low = 0;
        $high = $count - 1;
        $found = null;
        while ($low <= $high) {
            $middle = intdiv($low + $high, 2);
            $record = self::recordAt($handle, $offset + $middle * $recordLength, $recordLength);
            if ($record === null) {
                return null;
            }
            if (strcmp(substr($record, 0, $width), $address) <= 0) {
                $found = $record;
                $low = $middle + 1;
            } else {
                $high = $middle - 1;
            }
        }
        if ($found === null || strcmp(substr($found, $width, $width), $address) < 0) {
            return null;
        }
        $region = rtrim(substr($found, $width * 2), "\0");
        return $region === '' ? null : $region;
    }

    /** @param resource $handle */
    private static function recordAt($handle, int $offset, int $length): ?string
    {
        if (fseek($handle, $offset) !== 0) {
            return null;
        }
        $record = fread($handle, $length);
        return ($record === false || strlen($record) !== $length) ? null : $record;
    }

    /**
     * 读文件头并核对魔数与体量。
     *
     * @return array{0:int,1:int}|null [IPv4 条数, IPv6 条数]；不是本格式或长度对不上时 null
     */
    private static function header(string $indexPath): ?array
    {
        $handle = @fopen($indexPath, 'rb');
        if ($handle === false) {
            return null;
        }
        try {
            $head = fread($handle, self::HEADER_LENGTH);
            if ($head === false || strlen($head) !== self::HEADER_LENGTH
                || substr($head, 0, strlen(self::MAGIC)) !== self::MAGIC) {
                return null;
            }
            $counts = unpack('Nv4/Nv6', substr($head, strlen(self::MAGIC)));
            if ($counts === false) {
                return null;
            }
            $expected = self::HEADER_LENGTH
                + $counts['v4'] * (4 + 4 + self::REGION_WIDTH)
                + $counts['v6'] * (16 + 16 + self::REGION_WIDTH);
            // 被截断的索引会让二分读到半条记录，必须在这里就认出来。
            if (@filesize($indexPath) !== $expected) {
                return null;
            }
            return [(int) $counts['v4'], (int) $counts['v6']];
        } finally {
            fclose($handle);
        }
    }
}
