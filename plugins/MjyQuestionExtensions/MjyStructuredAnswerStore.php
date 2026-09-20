<?php

/**
 * 结构化作答副表：自增表格的每一个单元格一行，外加一行状态。
 *
 * 自然键沿用 [ADR 0003] 的约定 —— (引擎实例, 问卷, 代次, 答卷 id)，再加题目代码。
 * 题目代码而不是字段名：LimeSurvey 的答卷列名是 Q<qid> 系列，跨实例、跨导入批次都会变
 * （ADR 0005 场景二），只有题目代码稳定。
 * 代次来自 MjyPlatformBridge 的代次表：问卷重新激活后 response id 会从 1 重新计数。
 *
 *   {prefix}mjyquestionextensions_answer_cell   一个单元格一行
 *   {prefix}mjyquestionextensions_answer_state  一次作答一行（行数、是否通过校验、错误）
 */
class MjyStructuredAnswerStore
{
    public const CELL_TABLE = 'mjyquestionextensions_answer_cell';
    public const STATE_TABLE = 'mjyquestionextensions_answer_state';

    private const QUESTION_CODE_LENGTH = 64;
    private const COLUMN_CODE_LENGTH = 64;
    private const ERRORS_MAX_LENGTH = 1000;

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
        return $this->db->tablePrefix . self::CELL_TABLE;
    }

    public function stateTableName(): string
    {
        return $this->db->tablePrefix . self::STATE_TABLE;
    }

    public function ensureSchema(): void
    {
        $this->createIfMissing($this->tableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'id' => 'pk',
                'engine_instance_id' => 'string(64) NOT NULL',
                'survey_id' => 'integer NOT NULL',
                'generation' => 'string(36) NOT NULL',
                'response_id' => 'integer NOT NULL',
                'question_code' => 'string(' . self::QUESTION_CODE_LENGTH . ') NOT NULL',
                'row_index' => 'integer NOT NULL',
                'column_code' => 'string(' . self::COLUMN_CODE_LENGTH . ') NOT NULL',
                'cell_value' => 'text NULL',
                'updated_at' => 'datetime NOT NULL',
            ]);
            $this->db->createCommand()->createIndex(
                $table . '_cell',
                $table,
                'engine_instance_id,survey_id,generation,response_id,question_code,row_index,column_code',
                true
            );
        });

        $this->createIfMissing($this->stateTableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'id' => 'pk',
                'engine_instance_id' => 'string(64) NOT NULL',
                'survey_id' => 'integer NOT NULL',
                'generation' => 'string(36) NOT NULL',
                'response_id' => 'integer NOT NULL',
                'question_code' => 'string(' . self::QUESTION_CODE_LENGTH . ') NOT NULL',
                'row_count' => 'integer NOT NULL',
                'is_valid' => 'integer NOT NULL',
                'errors' => 'text NULL',
                'updated_at' => 'datetime NOT NULL',
            ]);
            $this->db->createCommand()->createIndex(
                $table . '_answer',
                $table,
                'engine_instance_id,survey_id,generation,response_id,question_code',
                true
            );
        });
    }

    /**
     * 写入一次作答：状态总是写，单元格只在校验通过时写。
     * 校验失败时刻意清空单元格 —— 引擎答卷表里还留着原始文本，平台必须能看出「这条不可信」。
     */
    public function recordAnswer(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        MjyValidationResult $result
    ): void {
        $this->replaceRows($surveyId, $generation, $responseId, $questionCode, $result->rows());
        $this->writeState($surveyId, $generation, $responseId, $questionCode, $result);
    }

    /**
     * @param array<int, array<string, string>> $rows
     * @return int 写入的单元格数
     */
    public function replaceRows(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        array $rows
    ): int {
        $transaction = $this->db->getCurrentTransaction() === null ? $this->db->beginTransaction() : null;
        try {
            $this->db->createCommand()->delete(
                $this->tableName(),
                $this->answerCondition(),
                $this->answerParams($surveyId, $generation, $responseId, $questionCode)
            );
            $written = 0;
            $now = gmdate('Y-m-d H:i:s');
            foreach ($rows as $rowIndex => $row) {
                foreach ($row as $columnCode => $value) {
                    $this->db->createCommand()->insert($this->tableName(), [
                        'engine_instance_id' => $this->engineInstanceId,
                        'survey_id' => $surveyId,
                        'generation' => $generation,
                        'response_id' => $responseId,
                        'question_code' => $questionCode,
                        'row_index' => (int) $rowIndex,
                        'column_code' => (string) $columnCode,
                        'cell_value' => $value,
                        'updated_at' => $now,
                    ]);
                    $written++;
                }
            }
            if ($transaction !== null) {
                $transaction->commit();
            }
            return $written;
        } catch (Throwable $exception) {
            if ($transaction !== null) {
                $transaction->rollback();
            }
            throw $exception;
        }
    }

    /**
     * @return array<int, array<string, string>> 按行序号排序的行
     */
    public function fetchRows(int $surveyId, string $generation, int $responseId, string $questionCode): array
    {
        $cells = $this->db->createCommand()
            ->select('row_index, column_code, cell_value')
            ->from($this->tableName())
            ->where($this->answerCondition(), $this->answerParams($surveyId, $generation, $responseId, $questionCode))
            ->order('row_index, id')
            ->queryAll();

        $rows = [];
        foreach ($cells as $cell) {
            $rows[(int) $cell['row_index']][(string) $cell['column_code']] = (string) $cell['cell_value'];
        }
        ksort($rows);
        return array_values($rows);
    }

    /**
     * @return array<string, mixed>|null
     */
    public function fetchState(int $surveyId, string $generation, int $responseId, string $questionCode): ?array
    {
        $row = $this->db->createCommand()
            ->select('*')
            ->from($this->stateTableName())
            ->where($this->answerCondition(), $this->answerParams($surveyId, $generation, $responseId, $questionCode))
            ->queryRow();
        return $row === false ? null : $row;
    }

    /**
     * @return int 删除的单元格数
     */
    public function purgeResponse(int $surveyId, string $generation, int $responseId): int
    {
        $condition = 'engine_instance_id = :instance AND survey_id = :sid'
            . ' AND generation = :gen AND response_id = :rid';
        $params = [
            ':instance' => $this->engineInstanceId,
            ':sid' => $surveyId,
            ':gen' => $generation,
            ':rid' => $responseId,
        ];
        $deleted = $this->db->createCommand()->delete($this->tableName(), $condition, $params);
        $this->db->createCommand()->delete($this->stateTableName(), $condition, $params);
        return $deleted;
    }

    private function writeState(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        MjyValidationResult $result
    ): void {
        $values = [
            'row_count' => $result->rowCount(),
            'is_valid' => $result->isValid() ? 1 : 0,
            'errors' => mb_substr($result->errorText(), 0, self::ERRORS_MAX_LENGTH, 'UTF-8'),
            'updated_at' => gmdate('Y-m-d H:i:s'),
        ];
        // 不能用 UPDATE 的影响行数判断「有没有这一行」：MySQL 在新旧值相同时返回 0。
        $existing = $this->fetchState($surveyId, $generation, $responseId, $questionCode);
        if ($existing !== null) {
            $this->db->createCommand()->update(
                $this->stateTableName(),
                $values,
                'id = :id',
                [':id' => $existing['id']]
            );
            return;
        }
        $this->db->createCommand()->insert($this->stateTableName(), $values + [
            'engine_instance_id' => $this->engineInstanceId,
            'survey_id' => $surveyId,
            'generation' => $generation,
            'response_id' => $responseId,
            'question_code' => $questionCode,
        ]);
    }

    private function answerCondition(): string
    {
        return 'engine_instance_id = :instance AND survey_id = :sid AND generation = :gen'
            . ' AND response_id = :rid AND question_code = :code';
    }

    /**
     * @return array<string, mixed>
     */
    private function answerParams(int $surveyId, string $generation, int $responseId, string $questionCode): array
    {
        return [
            ':instance' => $this->engineInstanceId,
            ':sid' => $surveyId,
            ':gen' => $generation,
            ':rid' => $responseId,
            ':code' => $questionCode,
        ];
    }

    /**
     * 并发首用时另一个请求可能已经建好表：建失败后再确认一次即可。
     */
    private function createIfMissing(string $table, callable $create): void
    {
        if ($this->db->getSchema()->getTable($table, true) !== null) {
            return;
        }
        try {
            $create($table);
        } catch (CDbException $exception) {
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }
}
