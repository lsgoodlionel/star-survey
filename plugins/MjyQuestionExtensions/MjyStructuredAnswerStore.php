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
 *
 * 两张表都带 structure_version：平台发布时声明的「结构版本」，答案按哪一版列定义写入
 * 就记哪一版。换了列定义之后，早先的答卷仍然能按它自己那一版读回来。
 * 契约见 platform/contracts/question-extension-tables-v1.md。
 */
class MjyStructuredAnswerStore
{
    public const CELL_TABLE = 'mjyquestionextensions_answer_cell';
    public const STATE_TABLE = 'mjyquestionextensions_answer_state';

    /** 结构版本列。 */
    public const STRUCTURE_VERSION_COLUMN = 'structure_version';

    /**
     * 本契约出现之前写下的行的结构版本：不是「第 0 版」，而是「不知道是哪一版」。
     * 迁移只能这样标：那些行的列定义已经无从考证。
     */
    public const LEGACY_STRUCTURE_VERSION = '0';

    private const QUESTION_CODE_LENGTH = 64;
    private const COLUMN_CODE_LENGTH = 64;
    private const STRUCTURE_VERSION_LENGTH = 32;
    private const ERRORS_MAX_LENGTH = 1000;
    /** 结构版本来自题目属性，也就是来自 .lss，必须当成外部数据消毒。 */
    private const STRUCTURE_VERSION_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/';

    /** @var CDbConnection */
    private $db;

    /** @var string */
    private $engineInstanceId;

    public function __construct(CDbConnection $db, string $engineInstanceId)
    {
        $this->db = $db;
        $this->engineInstanceId = $engineInstanceId;
    }

    /** 本实例标识，是两张表自然键的第一段（ADR 0003）。批量读取器按它过滤。 */
    public function engineInstanceId(): string
    {
        return $this->engineInstanceId;
    }

    public function tableName(): string
    {
        return $this->db->tablePrefix . self::CELL_TABLE;
    }

    public function stateTableName(): string
    {
        return $this->db->tablePrefix . self::STATE_TABLE;
    }

    /**
     * 消毒一个结构版本标识。
     *
     * 值来自题目属性（随 .lss 发布，作者端可控），所以进库前压成 `[A-Za-z0-9._-]{1,32}`；
     * 缺失或不合法一律回落到「不知道是哪一版」而不是猜一个版本号——失败关闭。
     *
     * @param string|null $raw
     */
    public static function normaliseStructureVersion($raw): string
    {
        $value = trim((string) $raw);
        return preg_match(self::STRUCTURE_VERSION_PATTERN, $value) === 1
            ? $value
            : self::LEGACY_STRUCTURE_VERSION;
    }

    /**
     * 建表，并把已经存在的旧表升级到当前契约（加列、回填）。
     * 两步分开：并发首用时建表可能被别的请求抢先，升级则必须对任何一条路径都跑到。
     */
    public function ensureSchema(): void
    {
        $this->createTables();
        $this->upgradeSchema();
    }

    /**
     * 把缺少 structure_version 的旧表补上这一列，并把既有行标成「不知道是哪一版」。
     *
     * @return string[] 实际升级过的表名
     */
    public function upgradeSchema(): array
    {
        $upgraded = [];
        foreach ([$this->tableName(), $this->stateTableName()] as $table) {
            if ($this->hasStructureVersion($table)) {
                continue;
            }
            $this->db->createCommand()->addColumn(
                $table,
                self::STRUCTURE_VERSION_COLUMN,
                'string(' . self::STRUCTURE_VERSION_LENGTH . ") NOT NULL DEFAULT '"
                    . self::LEGACY_STRUCTURE_VERSION . "'"
            );
            // DEFAULT 只管新行；已有行在 MariaDB 上会拿到默认值，在别的引擎上未必，显式回填一次。
            $this->db->createCommand()->update(
                $table,
                [self::STRUCTURE_VERSION_COLUMN => self::LEGACY_STRUCTURE_VERSION],
                self::STRUCTURE_VERSION_COLUMN . ' IS NULL OR ' . self::STRUCTURE_VERSION_COLUMN . " = ''"
            );
            $this->db->getSchema()->refresh();
            $upgraded[] = $table;
        }
        return $upgraded;
    }

    private function hasStructureVersion(string $table): bool
    {
        $schema = $this->db->getSchema()->getTable($table, true);
        return $schema !== null && isset($schema->columns[self::STRUCTURE_VERSION_COLUMN]);
    }

    private function createTables(): void
    {
        $this->createIfMissing($this->tableName(), function (string $table): void {
            $this->db->createCommand()->createTable($table, [
                'id' => 'pk',
                'engine_instance_id' => 'string(64) NOT NULL',
                'survey_id' => 'integer NOT NULL',
                'generation' => 'string(36) NOT NULL',
                'response_id' => 'integer NOT NULL',
                'question_code' => 'string(' . self::QUESTION_CODE_LENGTH . ') NOT NULL',
                self::STRUCTURE_VERSION_COLUMN => 'string(' . self::STRUCTURE_VERSION_LENGTH . ") NOT NULL DEFAULT '"
                    . self::LEGACY_STRUCTURE_VERSION . "'",
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
                self::STRUCTURE_VERSION_COLUMN => 'string(' . self::STRUCTURE_VERSION_LENGTH . ") NOT NULL DEFAULT '"
                    . self::LEGACY_STRUCTURE_VERSION . "'",
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
        string $structureVersion,
        MjyValidationResult $result
    ): void {
        $this->replaceRows($surveyId, $generation, $responseId, $questionCode, $structureVersion, $result->rows());
        $this->writeState($surveyId, $generation, $responseId, $questionCode, $structureVersion, $result);
    }

    /**
     * 一次作答的单元格整体替换：一条答卷的一道题只可能是一个结构版本
     * （引擎答卷列里只有一份信封），所以旧版本的单元格连同旧行一起删。
     *
     * @param array<int, array<string, string>> $rows
     * @return int 写入的单元格数
     */
    public function replaceRows(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        string $structureVersion,
        array $rows
    ): int {
        $version = self::normaliseStructureVersion($structureVersion);
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
                        self::STRUCTURE_VERSION_COLUMN => $version,
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
     * @param string|null $structureVersion 只要这一版的单元格；null＝不限版本
     * @return array<int, array<string, string>> 按行序号排序的行
     */
    public function fetchRows(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode,
        $structureVersion = null
    ): array {
        $condition = $this->answerCondition();
        $params = $this->answerParams($surveyId, $generation, $responseId, $questionCode);
        if ($structureVersion !== null) {
            $condition .= ' AND ' . self::STRUCTURE_VERSION_COLUMN . ' = :sver';
            $params[':sver'] = self::normaliseStructureVersion($structureVersion);
        }
        $cells = $this->db->createCommand()
            ->select('row_index, column_code, cell_value')
            ->from($this->tableName())
            ->where($condition, $params)
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
     * 这条答卷的这道题在副表里用的是哪些结构版本。
     * 正常只有一个；读到多个说明有人绕过 replaceRows 直接写库，读端必须能看出来。
     *
     * @return string[]
     */
    public function fetchStructureVersions(
        int $surveyId,
        string $generation,
        int $responseId,
        string $questionCode
    ): array {
        $rows = $this->db->createCommand()
            ->selectDistinct(self::STRUCTURE_VERSION_COLUMN)
            ->from($this->tableName())
            ->where($this->answerCondition(), $this->answerParams($surveyId, $generation, $responseId, $questionCode))
            ->order(self::STRUCTURE_VERSION_COLUMN)
            ->queryAll();
        return array_map('strval', array_column($rows, self::STRUCTURE_VERSION_COLUMN));
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
        string $structureVersion,
        MjyValidationResult $result
    ): void {
        $values = [
            self::STRUCTURE_VERSION_COLUMN => self::normaliseStructureVersion($structureVersion),
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
