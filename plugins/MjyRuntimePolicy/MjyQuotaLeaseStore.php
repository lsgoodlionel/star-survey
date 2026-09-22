<?php

/**
 * 名额租约：把"占名额"提前到进入考场之前，并且让并发请求排队。
 *
 * 引擎自带的配额是"不加锁 COUNT → 比较 qlimit → 事后写 submitdate"三步
 * （Quota.php:131、Quotas.php:518、em_manager_helper.php:5435），中间没有事务，
 * 并发下最后一个名额会同时发给多个人。
 *
 * 这里换成：每个配额有一行计数锚点，发放租约前先 `SELECT ... FOR UPDATE` 锁住
 * 它，所有争抢同一配额的请求因此被数据库串行化，判定与写入落在同一个事务里。
 * 租约有 TTL：只占位不交卷的人到点自动把名额还回去；交卷时转成已确认，之后
 * 永不归还。
 */
class MjyQuotaLeaseStore
{
    public const STATE_HELD = 'held';
    public const STATE_CONFIRMED = 'confirmed';
    public const STATE_RELEASED = 'released';
    public const STATE_EXPIRED = 'expired';

    private const QUOTA_TABLE = 'mjyruntimepolicy_quota';
    private const LEASE_TABLE = 'mjyruntimepolicy_lease';
    private const KEY_LENGTH = 191;

    /** @var CDbConnection */
    private $db;

    public function __construct(CDbConnection $db)
    {
        $this->db = $db;
    }

    public function quotaTableName(): string
    {
        return $this->db->tablePrefix . self::QUOTA_TABLE;
    }

    public function tableName(): string
    {
        return $this->db->tablePrefix . self::LEASE_TABLE;
    }

    public function ensureSchema(): void
    {
        $this->ensureTable($this->quotaTableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'quota_key' => 'string(' . self::KEY_LENGTH . ') NOT NULL',
                'survey_id' => 'integer NOT NULL',
                'slot_limit' => 'integer NOT NULL',
                'updated_at' => 'datetime NOT NULL',
                'PRIMARY KEY (quota_key)',
            ]);
        });
        $this->ensureTable($this->tableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'lease_id' => 'string(36) NOT NULL',
                'quota_key' => 'string(' . self::KEY_LENGTH . ') NOT NULL',
                'holder' => 'string(' . self::KEY_LENGTH . ') NOT NULL',
                'state' => 'string(16) NOT NULL',
                'acquired_at' => 'datetime NOT NULL',
                'expires_at' => 'datetime NOT NULL',
                'settled_at' => 'datetime NULL',
                'PRIMARY KEY (lease_id)',
            ]);
            $this->db->createCommand()->createIndex($table . '_active', $table, 'quota_key,state,expires_at');
            $this->db->createCommand()->createIndex($table . '_holder', $table, 'quota_key,holder,state');
        });
    }

    /**
     * 平台下发或调整一个配额的名额数。调整上限不影响已发出的租约。
     */
    public function defineQuota(string $quotaKey, int $surveyId, int $slotLimit): void
    {
        if ($slotLimit < 0) {
            throw new InvalidArgumentException("名额数不能为负，收到 {$slotLimit}");
        }
        $values = [
            'survey_id' => $surveyId,
            'slot_limit' => $slotLimit,
            'updated_at' => gmdate('Y-m-d H:i:s'),
        ];
        // 不能用 affected rows 判断"有没有这一行"：值没变化时 MySQL 返回 0。
        if ($this->findQuota($quotaKey) !== null) {
            $this->updateQuota($quotaKey, $values);
            return;
        }
        try {
            $this->db->createCommand()->insert($this->quotaTableName(), ['quota_key' => $quotaKey] + $values);
        } catch (CDbException $exception) {
            // 并发首次下发：另一个请求先插入了，改成更新。
            if ($this->findQuota($quotaKey) === null) {
                throw $exception;
            }
            $this->updateQuota($quotaKey, $values);
        }
    }

    /**
     * 发放一张租约；名额已满返回 null。同一个持有者重复调用返回同一张租约。
     *
     * @param string $nowUtc 权威时刻，只允许由 MjyServerClock 提供
     * @return array{lease_id: string, quota_key: string, holder: string, state: string, expires_at: string}|null
     */
    public function reserve(string $quotaKey, string $holder, int $ttlSeconds, string $nowUtc): ?array
    {
        if ($ttlSeconds <= 0) {
            throw new InvalidArgumentException("租约时长必须为正数，收到 {$ttlSeconds}");
        }
        if ($this->db->getCurrentTransaction() !== null) {
            // 已经在别人的事务里（例如引擎将来把钩子包进事务）：直接用外层事务，
            // 行锁一样有效，只是持有到外层提交为止。
            return $this->reserveLocked($quotaKey, $holder, $ttlSeconds, $nowUtc);
        }
        $transaction = $this->db->beginTransaction();
        try {
            $lease = $this->reserveLocked($quotaKey, $holder, $ttlSeconds, $nowUtc);
            $transaction->commit();
            return $lease;
        } catch (Throwable $exception) {
            $transaction->rollback();
            throw $exception;
        }
    }

    /**
     * 只有仍在持有中的租约能被确认。已经过期的租约名额可能已经给了别人，
     * 再确认回来就是超发——所以租约 TTL 必须长于单场考试的最长时长。
     */
    public function confirm(string $leaseId): bool
    {
        return $this->settle($leaseId, self::STATE_CONFIRMED, [self::STATE_HELD]);
    }

    public function release(string $leaseId): bool
    {
        return $this->settle($leaseId, self::STATE_RELEASED, [self::STATE_HELD]);
    }

    /**
     * 把过期未交卷的租约标成 expired。名额在 usedSlots 里本来就不再计入，
     * 这一步只是让状态可审计，由 cron 周期调用。
     *
     * @return int 本次回收的租约数
     */
    public function reap(string $quotaKey, string $nowUtc): int
    {
        return (int) $this->db->createCommand()->update(
            $this->tableName(),
            ['state' => self::STATE_EXPIRED, 'settled_at' => $nowUtc],
            'quota_key = :key AND state = :held AND expires_at <= :now',
            [':key' => $quotaKey, ':held' => self::STATE_HELD, ':now' => $nowUtc]
        );
    }

    /**
     * 已占用的名额 = 已确认的 + 未过期的持有中租约。
     */
    public function usedSlots(string $quotaKey, string $nowUtc): int
    {
        return (int) $this->db->createCommand()
            ->select('COUNT(*)')
            ->from($this->tableName())
            ->where(
                'quota_key = :key AND (state = :confirmed OR (state = :held AND expires_at > :now))',
                [
                    ':key' => $quotaKey,
                    ':confirmed' => self::STATE_CONFIRMED,
                    ':held' => self::STATE_HELD,
                    ':now' => $nowUtc,
                ]
            )
            ->queryScalar();
    }

    /**
     * 已确认（交卷）的租约数：访问规则用它算"这是第几次作答"。
     */
    public function confirmedCount(string $quotaKey): int
    {
        return (int) $this->db->createCommand()
            ->select('COUNT(*)')
            ->from($this->tableName())
            ->where('quota_key = :key AND state = :confirmed', [':key' => $quotaKey, ':confirmed' => self::STATE_CONFIRMED])
            ->queryScalar();
    }

    /**
     * 配额行不存在或上限不同就（重新）定义；已存在且一致时不写。
     */
    public function ensureQuota(string $quotaKey, int $surveyId, int $slotLimit): void
    {
        $existing = $this->findQuota($quotaKey);
        if ($existing !== null && (int) $existing['slot_limit'] === $slotLimit) {
            return;
        }
        $this->defineQuota($quotaKey, $surveyId, $slotLimit);
    }

    /**
     * @return array<string, mixed>|null
     */
    public function findLease(string $leaseId): ?array
    {
        $row = $this->db->createCommand()
            ->select('*')
            ->from($this->tableName())
            ->where('lease_id = :id', [':id' => $leaseId])
            ->queryRow();
        return $row === false ? null : $row;
    }

    /**
     * 持有者当前仍然有效的租约（已确认，或持有中且未过期）。
     *
     * @return array<string, mixed>|null
     */
    public function findActiveLease(string $quotaKey, string $holder, string $nowUtc): ?array
    {
        $row = $this->db->createCommand()
            ->select('*')
            ->from($this->tableName())
            ->where(
                'quota_key = :key AND holder = :holder'
                . ' AND (state = :confirmed OR (state = :held AND expires_at > :now))',
                [
                    ':key' => $quotaKey,
                    ':holder' => $holder,
                    ':confirmed' => self::STATE_CONFIRMED,
                    ':held' => self::STATE_HELD,
                    ':now' => $nowUtc,
                ]
            )
            ->order('acquired_at DESC')
            ->queryRow();
        return $row === false ? null : $row;
    }

    /**
     * @return array<string, mixed>|null
     */
    public function findQuota(string $quotaKey): ?array
    {
        $row = $this->db->createCommand()
            ->select('*')
            ->from($this->quotaTableName())
            ->where('quota_key = :key', [':key' => $quotaKey])
            ->queryRow();
        return $row === false ? null : $row;
    }

    /**
     * @param array<string, mixed> $values
     */
    private function updateQuota(string $quotaKey, array $values): void
    {
        $this->db->createCommand()->update($this->quotaTableName(), $values, 'quota_key = :key', [':key' => $quotaKey]);
    }

    public static function uuidV4(): string
    {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($bytes), 4));
    }

    /**
     * 在配额行的排它锁保护下完成"读上限 → 数已用 → 写租约"。
     *
     * @return array{lease_id: string, quota_key: string, holder: string, state: string, expires_at: string}|null
     */
    private function reserveLocked(string $quotaKey, string $holder, int $ttlSeconds, string $nowUtc): ?array
    {
        // 串行化点：所有争抢同一配额的事务在这里排队。
        $slotLimit = $this->db->createCommand(
            'SELECT slot_limit FROM ' . $this->quotaTableName() . ' WHERE quota_key = :key FOR UPDATE'
        )->queryScalar([':key' => $quotaKey]);
        if ($slotLimit === false) {
            throw new RuntimeException("配额未定义：{$quotaKey}");
        }
        $existing = $this->findActiveLease($quotaKey, $holder, $nowUtc);
        if ($existing !== null) {
            return $this->publicView($existing);
        }
        if ($this->usedSlots($quotaKey, $nowUtc) >= (int) $slotLimit) {
            return null;
        }
        $lease = [
            'lease_id' => self::uuidV4(),
            'quota_key' => $quotaKey,
            'holder' => $holder,
            'state' => self::STATE_HELD,
            'acquired_at' => $nowUtc,
            'expires_at' => MjyServerClock::plusSeconds($nowUtc, $ttlSeconds),
            'settled_at' => null,
        ];
        $this->db->createCommand()->insert($this->tableName(), $lease);
        return $this->publicView($lease);
    }

    /**
     * @param string[] $fromStates
     */
    private function settle(string $leaseId, string $toState, array $fromStates): bool
    {
        $placeholders = [];
        $conditionParams = [':id' => $leaseId];
        foreach ($fromStates as $index => $state) {
            $placeholders[] = ":from{$index}";
            $conditionParams[":from{$index}"] = $state;
        }
        $updated = $this->db->createCommand()->update(
            $this->tableName(),
            ['state' => $toState, 'settled_at' => gmdate('Y-m-d H:i:s')],
            'lease_id = :id AND state IN (' . implode(',', $placeholders) . ')',
            $conditionParams
        );
        return $updated > 0;
    }

    /**
     * @param array<string, mixed> $lease
     * @return array{lease_id: string, quota_key: string, holder: string, state: string, expires_at: string}
     */
    private function publicView(array $lease): array
    {
        return [
            'lease_id' => (string) $lease['lease_id'],
            'quota_key' => (string) $lease['quota_key'],
            'holder' => (string) $lease['holder'],
            'state' => (string) $lease['state'],
            'expires_at' => substr((string) $lease['expires_at'], 0, 19),
        ];
    }

    private function ensureTable(string $table, callable $create): void
    {
        if ($this->db->getSchema()->getTable($table, true) !== null) {
            return;
        }
        try {
            $create($table);
        } catch (CDbException $exception) {
            // 另一个请求并发建好了。
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }
}
