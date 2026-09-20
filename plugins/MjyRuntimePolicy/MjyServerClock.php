<?php

/**
 * 权威时钟：所有策略判定的"现在"只能来自数据库服务器。
 *
 * 引擎自己判断问卷到期用的是 Web 进程的 `gmdate()`
 * （application/controllers/survey/SurveyIndex.php:387）。单机没问题，
 * 但多台 Web 节点的时钟可以互相偏移，考试场景下偏移就是可以被利用的时间差。
 * 数据库是整套部署里唯一的单点，所以把它的 UTC 时刻定为唯一权威。
 *
 * 注意：这里从不读取请求里的任何时间字段。客户端时钟、隐藏域、Cookie 里的
 * 时间戳都不是输入。
 */
class MjyServerClock
{
    private const FORMAT = 'Y-m-d H:i:s';

    /** @var CDbConnection */
    private $db;

    public function __construct(CDbConnection $db)
    {
        $this->db = $db;
    }

    /**
     * 数据库服务器的当前 UTC 时刻，格式与引擎写答卷表时一致。
     */
    public function nowUtc(): string
    {
        return (string) $this->db->createCommand($this->nowExpression())->queryScalar();
    }

    /**
     * @param string $utc 'Y-m-d H:i:s' 形式的 UTC 时刻
     */
    public static function plusSeconds(string $utc, int $seconds): string
    {
        return gmdate(self::FORMAT, self::toTimestamp($utc) + $seconds);
    }

    /**
     * @return bool 左边的时刻是否严格晚于右边
     */
    public static function isAfter(string $utc, string $otherUtc): bool
    {
        return self::toTimestamp($utc) > self::toTimestamp($otherUtc);
    }

    public static function toTimestamp(string $utc): int
    {
        $timestamp = strtotime($utc . ' UTC');
        if ($timestamp === false) {
            throw new InvalidArgumentException("不是合法的 UTC 时刻：{$utc}");
        }
        return $timestamp;
    }

    /**
     * MariaDB 与 PostgreSQL 取 UTC 的写法不同，两者都不受会话时区影响。
     */
    private function nowExpression(): string
    {
        if ($this->db->getDriverName() === 'pgsql') {
            return "SELECT to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')";
        }
        return 'SELECT UTC_TIMESTAMP()';
    }
}
