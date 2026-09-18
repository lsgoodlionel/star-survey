<?php

/**
 * Compensation scanner: derives lifecycle facts from the response tables
 * themselves, so events lost by the engine (crash before afterSurveyComplete,
 * bulk deleteByPk, raw SQL) are still reported exactly once.
 */
class MjyCompletionScanner
{
    public const DEFAULT_BATCH_SIZE = 500;

    /** @var CDbConnection */
    private $db;

    /** @var MjyEventLog */
    private $eventLog;

    /** @var int */
    private $batchSize;

    public function __construct(CDbConnection $db, MjyEventLog $eventLog, int $batchSize = self::DEFAULT_BATCH_SIZE)
    {
        $this->db = $db;
        $this->eventLog = $eventLog;
        $this->batchSize = $batchSize;
    }

    /**
     * @return int number of events recovered for this survey's current responses table
     */
    public function scanSurvey(int $surveyId, string $generation): int
    {
        return $this->scanCompletions($surveyId, $generation) + $this->scanDeletions($surveyId, $generation);
    }

    /**
     * Completed responses (submitdate set) without a completion event.
     */
    private function scanCompletions(int $surveyId, string $generation): int
    {
        $responseTable = $this->responseTable($surveyId);
        if ($responseTable === null) {
            return 0;
        }
        $sql = 'SELECT r.id FROM ' . $this->db->quoteTableName($responseTable) . ' r'
            . ' WHERE r.submitdate IS NOT NULL AND NOT EXISTS ('
            . ' SELECT 1 FROM ' . $this->db->quoteTableName($this->eventLog->tableName()) . ' l'
            . ' WHERE l.event_type = :type AND l.survey_id = :sid AND l.generation = :gen AND l.response_id = r.id)'
            . ' ORDER BY r.id';

        $record = function (int $responseId) use ($surveyId, $generation): bool {
            return $this->eventLog->recordCompleted($surveyId, $generation, $responseId, MjyEventLog::SOURCE_SCANNER);
        };
        $params = [':sid' => $surveyId, ':gen' => $generation, ':type' => MjyEventLog::TYPE_COMPLETED];
        return $this->recordInBatches($sql, $params, $record);
    }

    /**
     * Responses the log knows about that no longer exist and have no tombstone.
     */
    private function scanDeletions(int $surveyId, string $generation): int
    {
        $responseTable = $this->responseTable($surveyId);
        if ($responseTable === null) {
            return 0;
        }
        $log = $this->db->quoteTableName($this->eventLog->tableName());
        $sql = 'SELECT DISTINCT l.response_id AS id FROM ' . $log . ' l'
            . ' WHERE l.survey_id = :sid AND l.generation = :gen AND l.event_type <> :live_type'
            . ' AND NOT EXISTS (SELECT 1 FROM ' . $this->db->quoteTableName($responseTable) . ' r'
            . ' WHERE r.id = l.response_id)'
            . ' AND NOT EXISTS (SELECT 1 FROM ' . $log . ' d'
            . ' WHERE d.event_type = :tombstone_type AND d.survey_id = l.survey_id'
            . ' AND d.generation = l.generation AND d.response_id = l.response_id)'
            . ' ORDER BY l.response_id';

        $record = function (int $responseId) use ($surveyId, $generation): bool {
            return $this->eventLog->recordDeleted($surveyId, $generation, $responseId, MjyEventLog::SOURCE_SCANNER);
        };
        $params = [
            ':sid' => $surveyId,
            ':gen' => $generation,
            ':live_type' => MjyEventLog::TYPE_DELETED,
            ':tombstone_type' => MjyEventLog::TYPE_DELETED,
        ];
        return $this->recordInBatches($sql, $params, $record);
    }

    /**
     * Re-runs the query until it returns no rows; each pass records one batch.
     * Each placeholder appears once per query (PostgreSQL native prepares reject reuse).
     *
     * @param array<string, int|string> $params
     */
    private function recordInBatches(string $sql, array $params, callable $record): int
    {
        $recorded = 0;
        do {
            $ids = $this->db->createCommand($sql . ' LIMIT ' . $this->batchSize)
                ->queryColumn($params);
            $recordedInBatch = 0;
            foreach ($ids as $responseId) {
                if ($record((int) $responseId)) {
                    $recordedInBatch++;
                }
            }
            $recorded += $recordedInBatch;
            // Stop when the batch was short, or made no progress (avoids an endless loop).
        } while (count($ids) === $this->batchSize && $recordedInBatch > 0);
        return $recorded;
    }

    private function responseTable(int $surveyId): ?string
    {
        $table = $this->db->tablePrefix . 'responses_' . $surveyId;
        return $this->db->getSchema()->getTable($table) === null ? null : $table;
    }
}
