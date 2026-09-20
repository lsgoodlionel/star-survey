<?php

/**
 * 平台下发给某份问卷的运行时策略。
 *
 * 刻意不用插件设置表：那张表没有唯一约束，并发写会产生多行，getGeneric() 随即
 * 返回数组（ADR 0003 里已经踩过一次）。这里用 survey_id 做主键。
 *
 * 字段含义：
 *   exam_duration_seconds  每场考试的时长，0 表示不限时；
 *   quota_slot_limit       硬名额上限，0 表示不设名额；
 *   lease_ttl_seconds      名额租约的存活时间，只占位不交卷的人到点归还名额。
 */
class MjySurveyPolicyStore
{
    private const TABLE = 'mjyruntimepolicy_survey_policy';

    /** @var CDbConnection */
    private $db;

    public function __construct(CDbConnection $db)
    {
        $this->db = $db;
    }

    public function tableName(): string
    {
        return $this->db->tablePrefix . self::TABLE;
    }

    public function ensureSchema(): void
    {
        $table = $this->tableName();
        if ($this->db->getSchema()->getTable($table, true) !== null) {
            return;
        }
        try {
            $this->db->createCommand()->createTable($table, [
                'survey_id' => 'integer NOT NULL',
                'exam_duration_seconds' => 'integer NOT NULL',
                'quota_slot_limit' => 'integer NOT NULL',
                'lease_ttl_seconds' => 'integer NOT NULL',
                'updated_at' => 'datetime NOT NULL',
                'PRIMARY KEY (survey_id)',
            ]);
        } catch (CDbException $exception) {
            // 另一个请求并发建好了。
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }

    public function save(int $surveyId, int $examDurationSeconds, int $quotaSlotLimit, int $leaseTtlSeconds): void
    {
        if ($examDurationSeconds < 0 || $quotaSlotLimit < 0) {
            throw new InvalidArgumentException('考试时长与名额上限不能为负。');
        }
        if ($leaseTtlSeconds <= 0) {
            throw new InvalidArgumentException("租约时长必须为正数，收到 {$leaseTtlSeconds}");
        }
        $values = [
            'exam_duration_seconds' => $examDurationSeconds,
            'quota_slot_limit' => $quotaSlotLimit,
            'lease_ttl_seconds' => $leaseTtlSeconds,
            'updated_at' => gmdate('Y-m-d H:i:s'),
        ];
        // 不能用 affected rows 判断"有没有这一行"：值没变化时 MySQL 返回 0。
        if ($this->find($surveyId) !== null) {
            $this->update($surveyId, $values);
            return;
        }
        try {
            $this->db->createCommand()->insert($this->tableName(), ['survey_id' => $surveyId] + $values);
        } catch (CDbException $exception) {
            // 并发首次下发：另一个请求先插入了，改成更新。
            if ($this->find($surveyId) === null) {
                throw $exception;
            }
            $this->update($surveyId, $values);
        }
    }

    /**
     * @return array{exam_duration_seconds: int, quota_slot_limit: int, lease_ttl_seconds: int}|null
     */
    public function find(int $surveyId): ?array
    {
        $row = $this->db->createCommand()
            ->select('exam_duration_seconds, quota_slot_limit, lease_ttl_seconds')
            ->from($this->tableName())
            ->where('survey_id = :sid', [':sid' => $surveyId])
            ->queryRow();
        if ($row === false) {
            return null;
        }
        return [
            'exam_duration_seconds' => (int) $row['exam_duration_seconds'],
            'quota_slot_limit' => (int) $row['quota_slot_limit'],
            'lease_ttl_seconds' => (int) $row['lease_ttl_seconds'],
        ];
    }

    /**
     * @param array<string, mixed> $values
     */
    private function update(int $surveyId, array $values): void
    {
        $this->db->createCommand()->update($this->tableName(), $values, 'survey_id = :sid', [':sid' => $surveyId]);
    }

    public function delete(int $surveyId): void
    {
        $this->db->createCommand()->delete($this->tableName(), 'survey_id = :sid', [':sid' => $surveyId]);
    }
}
