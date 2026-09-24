<?php

/**
 * 考场记录与到点强制交卷（WP-09.2）。
 *
 * 补上 ADR 0007 留下的那一项：截止时刻原本只有"到点即拒"。对考试来说那是错的——
 * 作答者已经落进答卷表的答案会永远停在 submitdate IS NULL，谁都不算他考过，
 * 等于白考一场。这里由**服务端**在到点时把 submitdate 写进去。
 *
 * 写进去的时刻是**截止时刻**，不是回收作业碰巧跑起来的时刻：作答者的时间就是在
 * 截止时刻用完的，而且这样结果与回收时机无关，同一场考试重跑多少次都一样。
 *
 * 每个 (引擎实例, 问卷, 考场身份) 一行，唯一索引挡住并发重复。截止时刻**写一次
 * 就不再改**：断线重连会再走一次 beforeSurveyPage，若在这里顺手更新截止时刻，
 * "断线一次多考半小时"就成了可复现的作弊手法。
 */
class MjyExamAttemptStore
{
    public const STATE_IN_PROGRESS = 'in_progress';
    public const STATE_SUBMITTED = 'submitted';
    public const STATE_FORCED = 'forced';

    private const TABLE = 'mjyruntimepolicy_exam_attempt';
    private const SESSION_KEY_LENGTH = 191;
    /** 一次回收最多处理这么多份，避免 cron 在大考后被一条语句拖死。 */
    private const REAP_BATCH = 500;

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
     * 记下（或补全）本次作答的考场记录。每翻一页都会进来一次，必须幂等。
     *
     * @param int|null $responseId 引擎的答卷行号，第一页提交之前还没有
     * @return array{session_key: string, response_id: int|null, deadline_at: string, state: string}
     */
    public function track(
        int $surveyId,
        string $sessionKey,
        ?int $responseId,
        string $deadlineUtc,
        string $nowUtc
    ): array {
        $existing = $this->find($surveyId, $sessionKey);
        if ($existing === null) {
            return $this->insert($surveyId, $sessionKey, $responseId, $deadlineUtc, $nowUtc);
        }
        // 已结的考场不再改动：回收作业不该去碰一份已经完成的答卷。
        // 截止时刻同样不动——只更新答卷行号这一件事。
        //
        // 答卷行号**跟随作答者当下正在填的那一份**，不是只认第一次见到的那个：
        // 断线重开（或 newtest=Y 重新开考）会让引擎换一行，答案在新的那一行里，
        // 强制交卷要交的就是它。行号来自服务端会话（responses_<sid>.srid），
        // 作答者改不了；写入本身又只动 submitdate IS NULL 的行，够不着别人已交的卷。
        if ($existing['state'] === self::STATE_IN_PROGRESS
            && $responseId !== null && $existing['response_id'] !== $responseId) {
            $this->db->createCommand()->update(
                $this->tableName(),
                ['response_id' => $responseId],
                'engine_instance_id = :instance AND survey_id = :sid AND session_key = :key',
                [':instance' => $this->engineInstanceId, ':sid' => $surveyId, ':key' => $sessionKey]
            );
            $existing['response_id'] = $responseId;
        }
        return $existing;
    }

    /**
     * @return array{session_key: string, response_id: int|null, deadline_at: string, state: string}|null
     */
    public function find(int $surveyId, string $sessionKey): ?array
    {
        $row = $this->db->createCommand()
            ->select('session_key, response_id, deadline_at, state')
            ->from($this->tableName())
            ->where(
                'engine_instance_id = :instance AND survey_id = :sid AND session_key = :key',
                [':instance' => $this->engineInstanceId, ':sid' => $surveyId, ':key' => $sessionKey]
            )
            ->queryRow();
        return $row === false ? null : $this->normalise($row);
    }

    /**
     * 作答者自己按时交了卷。之后回收作业不再管这一份。
     */
    public function markSubmitted(int $surveyId, string $sessionKey, string $nowUtc): bool
    {
        return $this->settle($surveyId, $sessionKey, self::STATE_SUBMITTED, $nowUtc);
    }

    /**
     * 到点强制交卷：把截止时刻写进答卷行的 submitdate，并把考场记录结掉。
     *
     * @return bool 是否真的落了一份答卷（没有答卷行、或答卷早已交过都返回 false，
     *              但考场记录一样会被结掉——它已经没有下文了）
     */
    public function forceSubmit(int $surveyId, string $sessionKey, string $nowUtc): bool
    {
        $attempt = $this->find($surveyId, $sessionKey);
        if ($attempt === null || $attempt['state'] !== self::STATE_IN_PROGRESS) {
            return false;
        }
        $submitted = $this->writeSubmitDate($surveyId, $attempt['response_id'], $attempt['deadline_at']);
        $this->settle($surveyId, $sessionKey, self::STATE_FORCED, $nowUtc);
        return $submitted;
    }

    /**
     * 还没结、且已经过了截止时刻的考场记录。
     *
     * @return array<int, array{session_key: string, response_id: int|null, deadline_at: string, state: string}>
     */
    public function expired(int $surveyId, string $nowUtc): array
    {
        $rows = $this->db->createCommand()
            ->select('session_key, response_id, deadline_at, state')
            ->from($this->tableName())
            ->where(
                'engine_instance_id = :instance AND survey_id = :sid AND state = :state AND deadline_at <= :now',
                [
                    ':instance' => $this->engineInstanceId, ':sid' => $surveyId,
                    ':state' => self::STATE_IN_PROGRESS, ':now' => $nowUtc,
                ]
            )
            ->order('deadline_at')
            ->limit(self::REAP_BATCH)
            ->queryAll();
        return array_map([$this, 'normalise'], $rows);
    }

    /**
     * 把这份问卷里所有到点未交的卷强制交掉。
     *
     * 这条路径是"人直接关掉了浏览器"唯一的兜底：没有后续请求，就没有
     * beforeSurveyPage 可挂。
     *
     * @return int 结掉了几份
     */
    public function reap(int $surveyId, string $nowUtc): int
    {
        $settled = 0;
        foreach ($this->expired($surveyId, $nowUtc) as $attempt) {
            $this->forceSubmit($surveyId, $attempt['session_key'], $nowUtc);
            $settled++;
        }
        return $settled;
    }

    // ------------------------------------------------------------ 内部

    /**
     * @return array{session_key: string, response_id: int|null, deadline_at: string, state: string}
     */
    private function insert(
        int $surveyId,
        string $sessionKey,
        ?int $responseId,
        string $deadlineUtc,
        string $nowUtc
    ): array {
        $record = [
            'engine_instance_id' => $this->engineInstanceId,
            'survey_id' => $surveyId,
            'session_key' => $sessionKey,
            'response_id' => $responseId,
            'deadline_at' => $deadlineUtc,
            'started_at' => $nowUtc,
            'state' => self::STATE_IN_PROGRESS,
        ];
        try {
            $this->db->createCommand()->insert($this->tableName(), $record);
        } catch (CDbException $exception) {
            // 并发首次进场：唯一索引只会留下一行，重读赢家。
            $winner = $this->find($surveyId, $sessionKey);
            if ($winner === null) {
                throw $exception;
            }
            return $winner;
        }
        return $this->normalise($record);
    }

    private function settle(int $surveyId, string $sessionKey, string $state, string $nowUtc): bool
    {
        $affected = $this->db->createCommand()->update(
            $this->tableName(),
            ['state' => $state, 'settled_at' => $nowUtc],
            'engine_instance_id = :instance AND survey_id = :sid AND session_key = :key AND state = :open',
            [
                ':instance' => $this->engineInstanceId, ':sid' => $surveyId,
                ':key' => $sessionKey, ':open' => self::STATE_IN_PROGRESS,
            ]
        );
        return $affected > 0;
    }

    /**
     * 只写还没交卷的那一行：作答者自己按时交的卷不能被改掉。
     */
    private function writeSubmitDate(int $surveyId, ?int $responseId, string $deadlineUtc): bool
    {
        if ($responseId === null) {
            return false;
        }
        $table = $this->db->tablePrefix . 'responses_' . $surveyId;
        if ($this->db->getSchema()->getTable($table, true) === null) {
            return false;
        }
        $affected = $this->db->createCommand()->update(
            $table,
            ['submitdate' => $deadlineUtc],
            'id = :id AND submitdate IS NULL',
            [':id' => $responseId]
        );
        return $affected > 0;
    }

    /**
     * @param array<string, mixed> $row
     * @return array{session_key: string, response_id: int|null, deadline_at: string, state: string}
     */
    private function normalise(array $row): array
    {
        return [
            'session_key' => (string) $row['session_key'],
            'response_id' => $row['response_id'] === null ? null : (int) $row['response_id'],
            // PostgreSQL 的 timestamp 会带微秒后缀，统一截到秒。
            'deadline_at' => substr((string) $row['deadline_at'], 0, 19),
            'state' => (string) $row['state'],
        ];
    }

    private function createTable(string $table): void
    {
        $this->db->createCommand()->createTable($table, [
            'id' => 'pk',
            'engine_instance_id' => 'string(64) NOT NULL',
            'survey_id' => 'integer NOT NULL',
            'session_key' => 'string(' . self::SESSION_KEY_LENGTH . ') NOT NULL',
            'response_id' => 'integer NULL',
            'deadline_at' => 'datetime NOT NULL',
            'started_at' => 'datetime NOT NULL',
            'state' => 'string(16) NOT NULL',
            'settled_at' => 'datetime NULL',
        ]);
        $this->db->createCommand()->createIndex(
            $table . '_session', $table, 'engine_instance_id,survey_id,session_key', true
        );
        // 回收作业按 (实例, 问卷, 状态, 截止时刻) 扫描。
        $this->db->createCommand()->createIndex(
            $table . '_due', $table, 'engine_instance_id,survey_id,state,deadline_at', false
        );
    }
}
