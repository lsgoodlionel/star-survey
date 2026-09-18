<?php

namespace ls\tests;

/**
 * P0-00.4: reliable response lifecycle events.
 *
 * The engine writes submitdate with Response::updateByPk() (no AR events) and
 * dispatches afterSurveyComplete outside any transaction, so a crash between
 * the two loses the completion signal. These tests pin the contract that the
 * event log plus the compensation scanner must still yield exactly one
 * completion event per response.
 */
class MjyPlatformBridgeTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyPlatformBridge';
    private const COMPLETED = 'response.completed';
    private const SAVED = 'response.saved';
    private const DELETED = 'response.deleted';

    /** @var \MjyPlatformBridge */
    private static $plugin;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        $pluginRecord = self::installAndActivatePlugin(self::PLUGIN_NAME);
        // Loading with an id instantiates the plugin and runs init(), so it
        // receives model events (afterResponseSave/Delete) during the tests.
        self::$plugin = App()->getPluginManager()->loadPlugin(self::PLUGIN_NAME, $pluginRecord->id);
        self::assertInstanceOf(\MjyPlatformBridge::class, self::$plugin, 'Plugin should load');
        self::$plugin->ensureSchema();

        self::importSurvey(self::$surveysFolder . '/limesurvey_survey_161359_quickTranslation.lss');
        (new \SurveyActivator(self::$testSurvey))->activate();
    }

    public static function tearDownAfterClass(): void
    {
        self::deActivatePlugin(self::PLUGIN_NAME);
        parent::tearDownAfterClass();
    }

    public function testPageSaveThroughActiveRecordLogsSavedEvent()
    {
        $responseId = $this->createResponse(false);

        $this->assertSame(1, $this->countEvents(self::SAVED, $responseId));
        $this->assertSame(0, $this->countEvents(self::COMPLETED, $responseId));
    }

    public function testCompletionHookLogsExactlyOneCompletedEvent()
    {
        $responseId = $this->createResponse(true);

        $this->fireSurveyComplete($responseId);
        $this->fireSurveyComplete($responseId);

        $this->assertSame(1, $this->countEvents(self::COMPLETED, $responseId));
        $this->assertSame('hook', $this->eventSource(self::COMPLETED, $responseId));
    }

    public function testScannerRecoversCompletionWhenHookWasLost()
    {
        // Simulates a crash after submitdate was written but before afterSurveyComplete.
        $responseId = $this->createResponse(true);
        $this->assertSame(0, $this->countEvents(self::COMPLETED, $responseId));

        $recovered = self::$plugin->runCompletionScan();

        $this->assertGreaterThanOrEqual(1, $recovered);
        $this->assertSame(1, $this->countEvents(self::COMPLETED, $responseId));
        $this->assertSame('scanner', $this->eventSource(self::COMPLETED, $responseId));
    }

    public function testScannerIsIdempotentAndIgnoresIncompleteResponses()
    {
        $completedId = $this->createResponse(true);
        $incompleteId = $this->createResponse(false);

        self::$plugin->runCompletionScan();
        $secondRun = self::$plugin->runCompletionScan();

        $this->assertSame(0, $secondRun);
        $this->assertSame(1, $this->countEvents(self::COMPLETED, $completedId));
        $this->assertSame(0, $this->countEvents(self::COMPLETED, $incompleteId));
    }

    public function testHookAfterScannerDoesNotDuplicateCompletion()
    {
        $responseId = $this->createResponse(true);
        self::$plugin->runCompletionScan();

        $this->fireSurveyComplete($responseId);

        $this->assertSame(1, $this->countEvents(self::COMPLETED, $responseId));
    }

    public function testActiveRecordDeleteLogsTombstone()
    {
        $responseId = $this->createResponse(true);

        \Response::model(self::$surveyId)->findByPk($responseId)->delete();

        $this->assertSame(1, $this->countEvents(self::DELETED, $responseId));
    }

    public function testScannerDetectsBulkDeleteThatBypassedModelEvents()
    {
        // Admin bulk delete (ResponsesController) uses deleteByPk: no AR events.
        $responseId = $this->createResponse(true);
        self::$plugin->runCompletionScan();

        \Response::model(self::$surveyId)->deleteByPk($responseId);
        self::$plugin->runCompletionScan();
        self::$plugin->runCompletionScan();

        $this->assertSame(1, $this->countEvents(self::DELETED, $responseId));
        $this->assertSame('scanner', $this->eventSource(self::DELETED, $responseId));
    }

    public function testReactivationStartsNewGenerationSoReusedIdsAreNotSwallowed()
    {
        // Deactivate + activate recreates responses_<sid>, so response ids restart at 1.
        $responseId = $this->createResponse(true);
        self::$plugin->runCompletionScan();
        $oldGeneration = self::$plugin->currentGeneration(self::$surveyId);

        self::dispatchPluginEvent(self::PLUGIN_NAME, 'afterSurveyActivate', [
            'surveyId' => self::$surveyId,
            'simulate' => false,
        ]);
        self::$plugin->runCompletionScan();

        $newGeneration = self::$plugin->currentGeneration(self::$surveyId);
        $this->assertNotSame($oldGeneration, $newGeneration);
        $this->assertSame(1, $this->countEvents(self::COMPLETED, $responseId));
    }

    public function testGenerationIsCreatedOnceEvenWhenAnotherWriterWonTheRace()
    {
        // Two requests racing on a survey activated before the plugin was installed:
        // the loser's insert must fall back to the winner's generation.
        $store = new \MjyGenerationStore(App()->getDb());
        $surveyId = 990001;
        App()->getDb()->createCommand()->delete($store->tableName(), 'survey_id = :sid', [':sid' => $surveyId]);

        $winner = $store->current($surveyId);
        $loser = $store->createIfMissing($surveyId, \MjyEventLog::uuidV4());

        $this->assertSame($winner, $loser);
        $this->assertSame($winner, $store->current($surveyId));
        $rows = App()->getDb()->createCommand()
            ->select('COUNT(*)')->from($store->tableName())
            ->where('survey_id = :sid', [':sid' => $surveyId])->queryScalar();
        $this->assertSame(1, (int) $rows);
    }

    public function testDeactivationScansBeforeResponsesTableIsRenamed()
    {
        // Crash lost the completion hook, and the survey is deactivated before the next cron.
        $responseId = $this->createResponse(true);

        self::dispatchPluginEvent(self::PLUGIN_NAME, 'beforeSurveyDeactivate', [
            'surveyId' => self::$surveyId,
        ]);

        $this->assertSame(1, $this->countEvents(self::COMPLETED, $responseId));
        $this->assertSame('scanner', $this->eventSource(self::COMPLETED, $responseId));
    }

    public function testHookRecordsEvenWhenSchemaWasNeverCreated()
    {
        $this->dropPluginTables();
        $responseId = $this->createResponse(true);

        $this->fireSurveyComplete($responseId);

        $this->assertSame(1, $this->countEvents(self::COMPLETED, $responseId));
    }

    public function testEventsCarryEngineInstanceAndUniqueEventId()
    {
        $responseId = $this->createResponse(true);
        $this->fireSurveyComplete($responseId);

        $row = $this->eventRows(self::COMPLETED, $responseId)[0];

        $this->assertSame(\MjyPlatformBridge::engineInstanceId(), $row['engine_instance_id']);
        $this->assertMatchesRegularExpression('/^[0-9a-f-]{36}$/', $row['event_id']);
        $this->assertSame((int) self::$surveyId, (int) $row['survey_id']);
    }

    /**
     * Mirrors the runtime path: Response::create() + encryptSave(), then submitdate via updateByPk.
     */
    private function createResponse(bool $isCompleted): int
    {
        $response = \Response::create(self::$surveyId);
        $response->startlanguage = 'en';
        $this->assertTrue($response->encryptSave(), 'Response should be saved');
        $responseId = (int) $response->id;

        if ($isCompleted) {
            \Response::model(self::$surveyId)->updateByPk($responseId, ['submitdate' => gmdate('Y-m-d H:i:s')]);
        }
        return $responseId;
    }

    private function fireSurveyComplete(int $responseId): void
    {
        self::dispatchPluginEvent(self::PLUGIN_NAME, 'afterSurveyComplete', [
            'surveyId' => self::$surveyId,
            'responseId' => $responseId,
        ]);
    }

    private function dropPluginTables(): void
    {
        $db = App()->getDb();
        foreach (['mjyplatformbridge_event_log', 'mjyplatformbridge_generation'] as $table) {
            if ($db->getSchema()->getTable($db->tablePrefix . $table, true) !== null) {
                $db->createCommand()->dropTable($db->tablePrefix . $table);
            }
        }
        $db->getSchema()->refresh();
        self::$plugin->resetSchemaState();
    }

    private function eventRows(string $eventType, int $responseId): array
    {
        return App()->db->createCommand()
            ->select('*')
            ->from(\MjyPlatformBridge::eventLogTable())
            ->where(
                'event_type = :type AND survey_id = :sid AND response_id = :rid AND generation = :gen',
                [
                    ':type' => $eventType,
                    ':sid' => self::$surveyId,
                    ':rid' => $responseId,
                    ':gen' => self::$plugin->currentGeneration(self::$surveyId),
                ]
            )
            ->queryAll();
    }

    private function countEvents(string $eventType, int $responseId): int
    {
        return count($this->eventRows($eventType, $responseId));
    }

    private function eventSource(string $eventType, int $responseId): string
    {
        return $this->eventRows($eventType, $responseId)[0]['source'];
    }
}
