<?php

namespace ls\tests;

/**
 * P0-00.4 投递中继：把引擎库里未投递的事件按顺序推给平台，只有平台确认接收后
 * 才标记已投递。投递失败不得丢事件，也不得重复产生业务效果（平台侧按 event_id
 * 去重，这里保证至少一次）。
 */
class MjyEventRelayTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyPlatformBridge';
    private const SURVEY_ID = 880001;
    private const GENERATION = 'gen-relay-test';

    /** @var \MjyPlatformBridge */
    private static $plugin;

    /** @var \MjyEventLog */
    private $eventLog;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        $pluginRecord = self::installAndActivatePlugin(self::PLUGIN_NAME);
        self::$plugin = \App()->getPluginManager()->loadPlugin(self::PLUGIN_NAME, $pluginRecord->id);
        self::$plugin->ensureSchema();
    }

    public static function tearDownAfterClass(): void
    {
        self::deActivatePlugin(self::PLUGIN_NAME);
        parent::tearDownAfterClass();
    }

    public function setUp(): void
    {
        parent::setUp();
        $this->eventLog = new \MjyEventLog(\App()->getDb(), 'engine-test');
        $this->eventLog->ensureSchema();
        // 中继是引擎实例级的，会取走表里所有未投递事件，
        // 因此这里清空整表，避免其他用例留下的事件影响断言。
        \App()->getDb()->createCommand()->truncateTable($this->eventLog->tableName());
    }

    public function testRelaysUndeliveredEventsInOrderAndMarksThemDelivered()
    {
        $this->recordCompleted(101);
        $this->recordCompleted(102);
        $transport = new FakeEventTransport();

        $sent = $this->relay($transport)->relay();

        $this->assertSame(2, $sent);
        $this->assertSame([101, 102], $this->sentResponseIds($transport));
        $this->assertSame(0, $this->undeliveredCount());
    }

    public function testAlreadyDeliveredEventsAreNotSentAgain()
    {
        $this->recordCompleted(103);
        $transport = new FakeEventTransport();
        $relay = $this->relay($transport);
        $relay->relay();

        $secondRun = $relay->relay();

        $this->assertSame(0, $secondRun);
        $this->assertCount(1, $transport->batches);
    }

    public function testTransportFailureKeepsEventsUndeliveredForTheNextRun()
    {
        $this->recordCompleted(104);
        $failing = new FakeEventTransport();
        $failing->failWith = new \RuntimeException('platform unreachable');

        // 失败向上抛出，由 cron 记录日志；事件保持未投递。
        try {
            $this->relay($failing)->relay();
            $this->fail('Relay should surface the transport failure');
        } catch (\RuntimeException $exception) {
            $this->assertSame('platform unreachable', $exception->getMessage());
        }
        $this->assertSame(1, $this->undeliveredCount());

        $working = new FakeEventTransport();
        $this->assertSame(1, $this->relay($working)->relay());
        $this->assertSame([104], $this->sentResponseIds($working));
    }

    public function testBatchesAreBoundedAndTheRestFollowsOnTheNextRun()
    {
        $this->recordCompleted(105);
        $this->recordCompleted(106);
        $this->recordCompleted(107);
        $transport = new FakeEventTransport();

        $sent = $this->relay($transport, 2)->relay();

        $this->assertSame(3, $sent);
        $this->assertSame([2, 1], array_map('count', $transport->batches));
        $this->assertSame(0, $this->undeliveredCount());
    }

    public function testFailureInTheSecondBatchKeepsOnlyThatBatchUndelivered()
    {
        $this->recordCompleted(108);
        $this->recordCompleted(109);
        $transport = new FakeEventTransport();
        $transport->failOnBatch = 2;

        try {
            $this->relay($transport, 1)->relay();
            $this->fail('Relay should surface the failure of the second batch');
        } catch (\RuntimeException $exception) {
            $this->assertStringContainsString('batch 2', $exception->getMessage());
        }
        // 第一批已确认投递，只有失败的那批留待重试。
        $this->assertCount(1, $transport->batches);
        $this->assertSame(1, $this->undeliveredCount());
    }

    public function testEnvelopeCarriesTheContractFields()
    {
        $this->recordCompleted(110);
        $transport = new FakeEventTransport();

        $this->relay($transport)->relay();

        $envelope = $transport->batches[0][0];
        $this->assertSame('response.completed', $envelope['eventType']);
        $this->assertSame(1, $envelope['schemaVersion']);
        $this->assertSame('engine-test', $envelope['engineInstanceId']);
        $this->assertSame(self::SURVEY_ID, $envelope['surveyId']);
        $this->assertSame(self::GENERATION, $envelope['generation']);
        $this->assertSame(110, $envelope['responseId']);
        $this->assertSame('hook', $envelope['source']);
        $this->assertMatchesRegularExpression('/^[0-9a-f-]{36}$/', $envelope['eventId']);
        $this->assertNotEmpty($envelope['occurredAt']);
    }

    private function relay(FakeEventTransport $transport, int $batchSize = 100): \MjyEventRelay
    {
        return new \MjyEventRelay(\App()->getDb(), $this->eventLog, $transport, $batchSize);
    }

    private function recordCompleted(int $responseId): void
    {
        $this->eventLog->recordCompleted(
            self::SURVEY_ID,
            self::GENERATION,
            $responseId,
            \MjyEventLog::SOURCE_HOOK
        );
    }

    private function undeliveredCount(): int
    {
        return (int) \App()->getDb()->createCommand()
            ->select('COUNT(*)')
            ->from($this->eventLog->tableName())
            ->where('survey_id = :sid AND delivered_at IS NULL', [':sid' => self::SURVEY_ID])
            ->queryScalar();
    }

    /**
     * @return int[]
     */
    private function sentResponseIds(FakeEventTransport $transport): array
    {
        $responseIds = [];
        foreach ($transport->batches as $batch) {
            foreach ($batch as $envelope) {
                $responseIds[] = $envelope['responseId'];
            }
        }
        return $responseIds;
    }
}
