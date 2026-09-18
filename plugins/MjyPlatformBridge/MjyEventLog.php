<?php

/**
 * Append-only response lifecycle event log stored in the engine database.
 *
 * Completion and deletion events carry a dedupe key so the completion hook,
 * the model events and the compensation scanner can all report the same fact
 * without producing duplicates.
 *
 * Response ids are only unique within one responses_<sid> table; deactivating
 * and re-activating a survey recreates the table and restarts ids. Every event
 * therefore carries the table "generation" (rotated on activation), and the
 * natural key is (engine instance, survey, generation, response id).
 */
class MjyEventLog
{
    public const TABLE = '{{mjyplatformbridge_event_log}}';

    public const TYPE_SAVED = 'response.saved';
    public const TYPE_COMPLETED = 'response.completed';
    public const TYPE_DELETED = 'response.deleted';

    public const SOURCE_HOOK = 'hook';
    public const SOURCE_SCANNER = 'scanner';

    private const DEDUPE_KEY_LENGTH = 191;

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
        return $this->db->tablePrefix . 'mjyplatformbridge_event_log';
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
            // Another request created it concurrently.
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }

    private function createTable(string $table): void
    {
        $this->db->createCommand()->createTable($table, [
            'id' => 'pk',
            'event_id' => 'string(36) NOT NULL',
            'event_type' => 'string(64) NOT NULL',
            'engine_instance_id' => 'string(64) NOT NULL',
            'survey_id' => 'integer NOT NULL',
            'generation' => 'string(36) NOT NULL',
            'response_id' => 'integer NOT NULL',
            'source' => 'string(16) NOT NULL',
            'dedupe_key' => 'string(' . self::DEDUPE_KEY_LENGTH . ') NULL',
            'occurred_at' => 'datetime NOT NULL',
            'delivered_at' => 'datetime NULL',
        ]);
        $command = $this->db->createCommand();
        $command->createIndex($table . '_dedupe', $table, 'dedupe_key', true);
        $command->createIndex($table . '_response', $table, 'survey_id,generation,response_id,event_type');
        $command->createIndex($table . '_undelivered', $table, 'delivered_at,id');
    }

    public function recordSaved(int $surveyId, string $generation, int $responseId, string $source): bool
    {
        return $this->insert(self::TYPE_SAVED, $surveyId, $generation, $responseId, $source, null);
    }

    public function recordCompleted(int $surveyId, string $generation, int $responseId, string $source): bool
    {
        return $this->insert(
            self::TYPE_COMPLETED,
            $surveyId,
            $generation,
            $responseId,
            $source,
            self::dedupeKey(self::TYPE_COMPLETED, $surveyId, $generation, $responseId)
        );
    }

    public function recordDeleted(int $surveyId, string $generation, int $responseId, string $source): bool
    {
        return $this->insert(
            self::TYPE_DELETED,
            $surveyId,
            $generation,
            $responseId,
            $source,
            self::dedupeKey(self::TYPE_DELETED, $surveyId, $generation, $responseId)
        );
    }

    public static function dedupeKey(string $type, int $surveyId, string $generation, int $responseId): string
    {
        return "{$type}:{$surveyId}:{$generation}:{$responseId}";
    }

    /**
     * @return bool true when a new row was written, false when the fact was already logged
     */
    private function insert(
        string $type,
        int $surveyId,
        string $generation,
        int $responseId,
        string $source,
        ?string $dedupeKey
    ): bool {
        if ($dedupeKey !== null && $this->exists($dedupeKey)) {
            return false;
        }
        try {
            $this->db->createCommand()->insert($this->tableName(), [
                'event_id' => self::uuidV4(),
                'event_type' => $type,
                'engine_instance_id' => $this->engineInstanceId,
                'survey_id' => $surveyId,
                'generation' => $generation,
                'response_id' => $responseId,
                'source' => $source,
                'dedupe_key' => $dedupeKey,
                'occurred_at' => gmdate('Y-m-d H:i:s'),
            ]);
        } catch (CDbException $exception) {
            // A concurrent writer may have logged the same fact between the check and the insert.
            if ($dedupeKey !== null && $this->exists($dedupeKey)) {
                return false;
            }
            throw $exception;
        }
        return true;
    }

    private function exists(string $dedupeKey): bool
    {
        return (int) $this->db->createCommand()
            ->select('COUNT(*)')
            ->from($this->tableName())
            ->where('dedupe_key = :key', [':key' => $dedupeKey])
            ->queryScalar() > 0;
    }

    public static function uuidV4(): string
    {
        $bytes = random_bytes(16);
        $bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
        $bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
        return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($bytes), 4));
    }
}
