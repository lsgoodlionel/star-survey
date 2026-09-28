<?php

/**
 * 成绩的存放（WP-10）。
 *
 * 成绩放在插件表里，**不往引擎的答卷表加列**。两个理由：
 *
 * 1. 答卷表的每一列都是作答者提交面的一部分，RemoteControl 的 `update_response`
 *    还能直接写它（ADR 0007 决定 4 已经记过：那条 API 绕开 beforeSurveyPage）。
 *    成绩挂在那里等于给了一条"自己改分"的路。
 * 2. 答卷表在问卷停用／重新启用时会被重建（ADR 0003 的代次问题），成绩会跟着没。
 *
 * 每份答卷一行，(引擎实例, 问卷, 答卷行号) 唯一。重判覆盖，不留两份：同一份卷子
 * 同时存在两个分数，谁都说不清哪个算数。
 *
 * 行里记着判分用的是哪一版答案键（key_digest）。答案键改过之后要能认出哪些成绩
 * 是旧的——否则一场考试改了答案键，前后两批人的分数没法比，也查不出为什么。
 */
class MjyExamScoreStore
{
    private const TABLE = 'mjyruntimepolicy_exam_score';
    private const DIGEST_LENGTH = 64;

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
     * 写入（或覆盖）一份成绩。
     */
    public function save(
        int $surveyId,
        int $responseId,
        MjyExamResult $result,
        string $keyDigest,
        string $nowUtc
    ): void {
        $values = [
            'score' => $result->score(),
            'max_score' => $result->maxScore(),
            'correct_count' => $result->correctCount(),
            'question_count' => $result->questionCount(),
            'key_digest' => $keyDigest,
            'detail' => $result->detailJson(),
            'graded_at' => $nowUtc,
        ];
        // 先查后写：MySQL 的 affected rows 在"更新成同样的值"时是 0，
        // 拿它判断有没有这一行会误判（与 MjySurveyPolicyStore 同一个坑）。
        if ($this->find($surveyId, $responseId) !== null) {
            $this->db->createCommand()->update($this->tableName(), $values, $this->where(), $this->params($surveyId, $responseId));
            return;
        }
        $this->db->createCommand()->insert($this->tableName(), $values + [
            'engine_instance_id' => $this->engineInstanceId,
            'survey_id' => $surveyId,
            'response_id' => $responseId,
        ]);
    }

    /**
     * @return array{score: float, max_score: float, correct_count: int, question_count: int,
     *               key_digest: string, detail: string, graded_at: string}|null
     */
    public function find(int $surveyId, int $responseId): ?array
    {
        $row = $this->db->createCommand()
            ->select('score, max_score, correct_count, question_count, key_digest, detail, graded_at')
            ->from($this->tableName())
            ->where($this->where(), $this->params($surveyId, $responseId))
            ->queryRow();
        if ($row === false) {
            return null;
        }
        return [
            'score' => (float) $row['score'],
            'max_score' => (float) $row['max_score'],
            'correct_count' => (int) $row['correct_count'],
            'question_count' => (int) $row['question_count'],
            'key_digest' => (string) $row['key_digest'],
            'detail' => (string) $row['detail'],
            // PostgreSQL 的 timestamp 会带微秒后缀，统一截到秒。
            'graded_at' => substr((string) $row['graded_at'], 0, 19),
        ];
    }

    public function countFor(int $surveyId): int
    {
        return (int) $this->db->createCommand()
            ->select('COUNT(*)')->from($this->tableName())
            ->where('engine_instance_id = :instance AND survey_id = :sid',
                [':instance' => $this->engineInstanceId, ':sid' => $surveyId])
            ->queryScalar();
    }

    /**
     * 这份问卷里，不是按当前答案键判出来的那些答卷。
     *
     * @return int[] 答卷行号
     */
    public function gradedWithOtherKey(int $surveyId, string $currentDigest): array
    {
        $rows = $this->db->createCommand()
            ->select('response_id')->from($this->tableName())
            ->where('engine_instance_id = :instance AND survey_id = :sid AND key_digest <> :digest',
                [':instance' => $this->engineInstanceId, ':sid' => $surveyId, ':digest' => $currentDigest])
            ->order('response_id')
            ->queryColumn();
        return array_map('intval', $rows);
    }

    private function where(): string
    {
        return 'engine_instance_id = :instance AND survey_id = :sid AND response_id = :rid';
    }

    /**
     * @return array<string, mixed>
     */
    private function params(int $surveyId, int $responseId): array
    {
        return [':instance' => $this->engineInstanceId, ':sid' => $surveyId, ':rid' => $responseId];
    }

    private function createTable(string $table): void
    {
        $this->db->createCommand()->createTable($table, [
            'id' => 'pk',
            'engine_instance_id' => 'string(64) NOT NULL',
            'survey_id' => 'integer NOT NULL',
            'response_id' => 'integer NOT NULL',
            'score' => 'decimal(10,2) NOT NULL',
            'max_score' => 'decimal(10,2) NOT NULL',
            'correct_count' => 'integer NOT NULL',
            'question_count' => 'integer NOT NULL',
            'key_digest' => 'string(' . self::DIGEST_LENGTH . ') NOT NULL',
            'detail' => 'text NOT NULL',
            'graded_at' => 'datetime NOT NULL',
        ]);
        $this->db->createCommand()->createIndex(
            $table . '_response', $table, 'engine_instance_id,survey_id,response_id', true
        );
    }
}
