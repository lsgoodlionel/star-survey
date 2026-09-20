<?php

/**
 * 把引擎库中未投递的事件按 id 升序推送给平台，成功后标记 delivered_at。
 *
 * 语义为至少一次：平台确认前不标记，因此进程中断或平台失败都只会造成重复投递，
 * 不会丢事件；平台侧按 eventId 去重。
 */
class MjyEventRelay
{
    public const DEFAULT_BATCH_SIZE = 100;
    public const SCHEMA_VERSION = 1;

    /** @var CDbConnection */
    private $db;

    /** @var MjyEventLog */
    private $eventLog;

    /** @var MjyEventTransport */
    private $transport;

    /** @var int */
    private $batchSize;

    public function __construct(
        CDbConnection $db,
        MjyEventLog $eventLog,
        MjyEventTransport $transport,
        int $batchSize = self::DEFAULT_BATCH_SIZE
    ) {
        $this->db = $db;
        $this->eventLog = $eventLog;
        $this->transport = $transport;
        $this->batchSize = $batchSize;
    }

    /**
     * @return int 已确认投递的事件数
     * @throws Throwable 投递失败时向上抛出，未投递的事件留待下轮
     */
    public function relay(): int
    {
        $delivered = 0;
        while (true) {
            $rows = $this->undeliveredBatch();
            if ($rows === []) {
                return $delivered;
            }
            $this->transport->send(array_map([$this, 'toEnvelope'], $rows));
            $this->markDelivered(array_column($rows, 'id'));
            $delivered += count($rows);
        }
    }

    /**
     * @return array<int, array<string, mixed>>
     */
    private function undeliveredBatch(): array
    {
        return $this->db->createCommand()
            ->select('*')
            ->from($this->eventLog->tableName())
            ->where('delivered_at IS NULL')
            ->order('id')
            ->limit($this->batchSize)
            ->queryAll();
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function toEnvelope(array $row): array
    {
        return [
            'eventId' => $row['event_id'],
            'eventType' => $row['event_type'],
            'schemaVersion' => self::SCHEMA_VERSION,
            'engineInstanceId' => $row['engine_instance_id'],
            'surveyId' => (int) $row['survey_id'],
            'generation' => $row['generation'],
            'responseId' => (int) $row['response_id'],
            'source' => $row['source'],
            'occurredAt' => $row['occurred_at'],
        ];
    }

    /**
     * @param array<int, int|string> $ids
     */
    private function markDelivered(array $ids): void
    {
        $this->db->createCommand()->update(
            $this->eventLog->tableName(),
            ['delivered_at' => gmdate('Y-m-d H:i:s')],
            ['in', 'id', array_map('intval', $ids)]
        );
    }
}
