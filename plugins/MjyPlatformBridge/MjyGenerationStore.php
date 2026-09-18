<?php

/**
 * Current responses-table generation per survey.
 *
 * Kept in its own table with survey_id as primary key: plugin settings have no
 * unique constraint, so concurrent first use could store two generations and
 * make getGeneric() return an array. Creation is insert-then-reselect, so the
 * loser of a race adopts the winner's value.
 */
class MjyGenerationStore
{
    /** @var CDbConnection */
    private $db;

    public function __construct(CDbConnection $db)
    {
        $this->db = $db;
    }

    public function tableName(): string
    {
        return $this->db->tablePrefix . 'mjyplatformbridge_generation';
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
                'generation' => 'string(36) NOT NULL',
                'rotated_at' => 'datetime NOT NULL',
                'PRIMARY KEY (survey_id)',
            ]);
        } catch (CDbException $exception) {
            // Another request created it concurrently.
            if ($this->db->getSchema()->getTable($table, true) === null) {
                throw $exception;
            }
        }
        $this->db->getSchema()->refresh();
    }

    public function current(int $surveyId): string
    {
        $generation = $this->find($surveyId);
        return $generation ?? $this->createIfMissing($surveyId, MjyEventLog::uuidV4());
    }

    /**
     * @return string the stored generation: $candidate, or the value another writer stored first
     */
    public function createIfMissing(int $surveyId, string $candidate): string
    {
        $existing = $this->find($surveyId);
        if ($existing !== null) {
            return $existing;
        }
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

    /**
     * Starts a new generation; called when the survey (re)activates and a new
     * responses_<sid> table with restarted ids is created.
     */
    public function rotate(int $surveyId): string
    {
        $generation = MjyEventLog::uuidV4();
        $updated = $this->db->createCommand()->update(
            $this->tableName(),
            ['generation' => $generation, 'rotated_at' => gmdate('Y-m-d H:i:s')],
            'survey_id = :sid',
            [':sid' => $surveyId]
        );
        return $updated > 0 ? $generation : $this->createIfMissing($surveyId, $generation);
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
