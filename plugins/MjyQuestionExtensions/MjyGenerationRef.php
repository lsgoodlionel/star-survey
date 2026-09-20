<?php

/**
 * 答卷表代次的读取端。
 *
 * 代次由 MjyPlatformBridge 在 afterSurveyActivate 时轮换（[ADR 0003] 决定 3），
 * 存在 {prefix}mjyplatformbridge_generation。这里**故意只读同一张表**，而不是
 * 另建一套代次：两个插件的自然键必须能直接对账，否则事件流与副表对不上。
 *
 * 不直接 new MjyGenerationStore：那个类由插件管理器在加载 MjyPlatformBridge 时
 * 才 Yii::import 进来，依赖加载顺序；这里只依赖表结构这一条契约。
 */
class MjyGenerationRef
{
    public const TABLE = 'mjyplatformbridge_generation';

    /** @var CDbConnection */
    private $db;

    /** @var bool 建表只确认一次：getTable(..., true) 会强制刷新元数据缓存 */
    private $isSchemaReady = false;

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
        if ($this->isSchemaReady) {
            return;
        }
        $table = $this->tableName();
        if ($this->db->getSchema()->getTable($table, true) !== null) {
            $this->isSchemaReady = true;
            return;
        }
        try {
            $this->db->createCommand()->createTable($table, [
                'survey_id' => 'integer NOT NULL',
                'generation' => 'string(36) NOT NULL',
                'rotated_at' => 'datetime NOT NULL',
                'PRIMARY KEY (survey_id)',
            ]);
        } catch (CDbException $exception) {
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
        $this->isSchemaReady = true;
    }

    /**
     * 当前代次；不存在时「插入失败即重读」，并发首用只会留下一行。
     */
    public function current(int $surveyId): string
    {
        $this->ensureSchema();
        $existing = $this->find($surveyId);
        if ($existing !== null) {
            return $existing;
        }
        $candidate = self::uuidV4();
        try {
            $this->db->createCommand()->insert($this->tableName(), [
                'survey_id' => $surveyId,
                'generation' => $candidate,
                'rotated_at' => gmdate('Y-m-d H:i:s'),
            ]);
            return $candidate;
        } catch (CDbException $exception) {
            $winner = $this->find($surveyId);
            if ($winner === null) {
                throw $exception;
            }
            return $winner;
        }
    }

    public static function uuidV4(): string
    {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($bytes), 4));
    }

    private function find(int $surveyId): ?string
    {
        $generation = $this->db->createCommand()
            ->select('generation')
            ->from($this->tableName())
            ->where('survey_id = :sid', [':sid' => $surveyId])
            ->queryScalar();
        return $generation === false ? null : (string) $generation;
    }
}
