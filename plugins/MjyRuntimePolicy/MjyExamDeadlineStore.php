<?php

/**
 * 每场考试的截止时刻，一次写入、永不延长。
 *
 * 自然键是（引擎实例, 问卷, 会话键）。会话键优先取准考证 token，没有 token 时
 * 退化为 PHP 会话 id：这决定了"重开浏览器算不算新的一场考试"。token 场景下
 * 换浏览器、清 Cookie、断线重连都命中同一行，拿到的还是首次进场时定死的截止
 * 时刻。
 *
 * 写入用"先查、插入、冲突即重读"，并发首次进场只会留下一行（与 ADR 0003 里
 * 代次表同样的理由：不能放进插件设置表，那里没有唯一约束）。
 */
class MjyExamDeadlineStore
{
    private const TABLE = 'mjyruntimepolicy_deadline';
    private const SESSION_KEY_LENGTH = 191;

    /** @var CDbConnection */
    private $db;

    /** @var string */
    private $engineInstanceId;

    public function __construct(CDbConnection $db, string $engineInstanceId)
    {
        $this->db = $db;
        $this->engineInstanceId = $engineInstanceId;
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
            $this->createTable($table);
        } catch (CDbException $exception) {
            // 另一个请求并发建好了。
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }

    /**
     * 首次进场时按服务端时刻定死截止时刻；已经有记录就原样返回。
     *
     * @param string $nowUtc 权威时刻，只允许由 MjyServerClock 提供
     * @return array{session_key: string, started_at: string, deadline_at: string, duration_seconds: int}
     */
    public function start(int $surveyId, string $sessionKey, int $durationSeconds, string $nowUtc): array
    {
        if ($durationSeconds <= 0) {
            throw new InvalidArgumentException("考试时长必须为正数，收到 {$durationSeconds}");
        }
        $existing = $this->find($surveyId, $sessionKey);
        if ($existing !== null) {
            return $existing;
        }
        $record = [
            'engine_instance_id' => $this->engineInstanceId,
            'survey_id' => $surveyId,
            'session_key' => $sessionKey,
            'started_at' => $nowUtc,
            'deadline_at' => MjyServerClock::plusSeconds($nowUtc, $durationSeconds),
            'duration_seconds' => $durationSeconds,
        ];
        try {
            $this->db->createCommand()->insert($this->tableName(), $record);
        } catch (CDbException $exception) {
            $winner = $this->find($surveyId, $sessionKey);
            if ($winner === null) {
                throw $exception;
            }
            return $winner;
        }
        return $this->normalise($record);
    }

    /**
     * @return array{session_key: string, started_at: string, deadline_at: string, duration_seconds: int}|null
     */
    public function find(int $surveyId, string $sessionKey): ?array
    {
        $row = $this->db->createCommand()
            ->select('session_key, started_at, deadline_at, duration_seconds')
            ->from($this->tableName())
            ->where(
                'engine_instance_id = :engine AND survey_id = :sid AND session_key = :key',
                [':engine' => $this->engineInstanceId, ':sid' => $surveyId, ':key' => $sessionKey]
            )
            ->queryRow();
        return $row === false ? null : $this->normalise($row);
    }

    /**
     * @param array<string, mixed> $row
     * @return array{session_key: string, started_at: string, deadline_at: string, duration_seconds: int}
     */
    private function normalise(array $row): array
    {
        return [
            'session_key' => (string) $row['session_key'],
            // PostgreSQL 的 timestamp 会带微秒后缀，统一截到秒。
            'started_at' => substr((string) $row['started_at'], 0, 19),
            'deadline_at' => substr((string) $row['deadline_at'], 0, 19),
            'duration_seconds' => (int) $row['duration_seconds'],
        ];
    }

    private function createTable(string $table): void
    {
        $this->db->createCommand()->createTable($table, [
            'id' => 'pk',
            'engine_instance_id' => 'string(64) NOT NULL',
            'survey_id' => 'integer NOT NULL',
            'session_key' => 'string(' . self::SESSION_KEY_LENGTH . ') NOT NULL',
            'started_at' => 'datetime NOT NULL',
            'deadline_at' => 'datetime NOT NULL',
            'duration_seconds' => 'integer NOT NULL',
        ]);
        $this->db->createCommand()->createIndex(
            $table . '_session',
            $table,
            'engine_instance_id,survey_id,session_key',
            true
        );
    }
}
