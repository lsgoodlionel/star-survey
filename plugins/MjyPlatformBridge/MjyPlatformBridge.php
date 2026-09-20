<?php

/**
 * MJY platform bridge (P0 prototype, WP-23 / R23-04).
 *
 * Records response lifecycle facts in an engine-side event log so the
 * platform can consume them at least once:
 *  - afterResponseSave / afterSurveyDynamicSave: page saves and data entry
 *  - afterSurveyComplete: completion hint (not transactional with submitdate)
 *  - afterResponseDelete / afterSurveyDynamicDelete: per-record deletes
 *  - cron: compensation scan for completions and bulk deletes that bypass
 *    model events (submitdate via updateByPk, deleteByPk in admin bulk delete),
 *    then relays undelivered events to the platform
 *  - beforeSurveyDeactivate: final scan before responses_<sid> is renamed away
 *  - afterSurveyActivate: rotates the responses table generation, because
 *    re-activation recreates responses_<sid> and restarts response ids
 *
 * Failures are logged and never interrupt the respondent's or admin's request.
 */
class MjyPlatformBridge extends \LimeSurvey\PluginManager\PluginBase
{
    public const DEFAULT_ENGINE_INSTANCE_ID = 'local-dev';
    private const ENGINE_INSTANCE_ENV = 'MJY_ENGINE_INSTANCE_ID';
    private const EVENTS_URL_ENV = 'MJY_PLATFORM_EVENTS_URL';
    private const EVENTS_SECRET_ENV = 'MJY_PLATFORM_EVENTS_SECRET';
    private const EVENTS_CLIENT_CERT_ENV = 'MJY_PLATFORM_CLIENT_CERT';
    private const LOG_CATEGORY = 'plugin.MjyPlatformBridge';

    protected $storage = 'DbStorage';
    protected static $description = 'MJY: reliable response lifecycle event log';
    protected static $name = 'MjyPlatformBridge';

    /** @var string[] */
    public $allowedPublicMethods = [];

    /** @var MjyEventLog|null */
    private $eventLog;

    /** @var MjyGenerationStore|null */
    private $generations;

    /** @var bool */
    private $isSchemaReady = false;

    public function init()
    {
        $this->subscribe('beforeActivate');
        $this->subscribe('afterResponseSave');
        $this->subscribe('afterSurveyDynamicSave');
        $this->subscribe('afterResponseDelete');
        $this->subscribe('afterSurveyDynamicDelete');
        $this->subscribe('afterSurveyComplete');
        $this->subscribe('afterSurveyActivate');
        $this->subscribe('beforeSurveyDeactivate');
        $this->subscribe('cron');
    }

    public static function engineInstanceId(): string
    {
        $configured = getenv(self::ENGINE_INSTANCE_ENV);
        return ($configured === false || $configured === '') ? self::DEFAULT_ENGINE_INSTANCE_ID : $configured;
    }

    public static function eventLogTable(): string
    {
        return MjyEventLog::TABLE;
    }

    public function beforeActivate()
    {
        $this->safely(function () {
            $this->ensureSchema();
        });
    }

    public function ensureSchema(): void
    {
        $this->eventLog()->ensureSchema();
        $this->generations()->ensureSchema();
        $this->isSchemaReady = true;
    }

    /**
     * Forget that the schema was verified in this request (tests drop tables).
     */
    public function resetSchemaState(): void
    {
        $this->isSchemaReady = false;
    }

    public function afterResponseSave()
    {
        $this->safely(function () {
            $this->recordSaved($this->getEvent()->get('dynamicId'), $this->getEvent()->get('model'));
        });
    }

    public function afterSurveyDynamicSave()
    {
        $this->safely(function () {
            $this->recordSaved($this->getEvent()->get('surveyId'), $this->getEvent()->get('model'));
        });
    }

    public function afterResponseDelete()
    {
        $this->safely(function () {
            $this->recordDeleted($this->getEvent()->get('dynamicId'), $this->getEvent()->get('model'));
        });
    }

    public function afterSurveyDynamicDelete()
    {
        $this->safely(function () {
            $this->recordDeleted($this->getEvent()->get('surveyId'), $this->getEvent()->get('model'));
        });
    }

    public function afterSurveyComplete()
    {
        $this->safely(function () {
            $surveyId = $this->getEvent()->get('surveyId');
            $responseId = $this->getEvent()->get('responseId');
            // Preview of an inactive survey has no stored response.
            if (empty($surveyId) || empty($responseId)) {
                return;
            }
            $this->readyEventLog()->recordCompleted(
                (int) $surveyId,
                $this->currentGeneration((int) $surveyId),
                (int) $responseId,
                MjyEventLog::SOURCE_HOOK
            );
        });
    }

    public function afterSurveyActivate()
    {
        $this->safely(function () {
            $surveyId = $this->getEvent()->get('surveyId');
            if (empty($surveyId) || $this->getEvent()->get('simulate')) {
                return;
            }
            $this->ensureSchemaOnce();
            $this->generations()->rotate((int) $surveyId);
        });
    }

    /**
     * Deactivation renames responses_<sid> to old_responses_*; anything the
     * cron has not recovered yet must be recovered now or never.
     */
    public function beforeSurveyDeactivate()
    {
        $this->safely(function () {
            $surveyId = $this->getEvent()->get('surveyId');
            if (empty($surveyId)) {
                return;
            }
            $this->scanSurvey((int) $surveyId);
        });
    }

    public function cron()
    {
        $this->safely(function () {
            $this->runCompletionScan();
        });
        // Relayed separately: a platform outage must not stop the scan that
        // keeps the log complete.
        $this->safely(function () {
            $this->relayEvents();
        });
    }

    /**
     * Pushes undelivered events to the platform. Without an endpoint configured
     * the events simply stay in the log (private deployments may pull instead).
     *
     * @return int number of events the platform confirmed
     */
    public function relayEvents(): int
    {
        $transport = $this->transport();
        if ($transport === null) {
            return 0;
        }
        $relay = new MjyEventRelay(App()->getDb(), $this->readyEventLog(), $transport);
        return $relay->relay();
    }

    public function currentGeneration(int $surveyId): string
    {
        $this->ensureSchemaOnce();
        return $this->generations()->current($surveyId);
    }

    /**
     * Scans every active survey; a failure on one survey is logged and does
     * not stop the others.
     *
     * @return int number of events recovered by the scan
     */
    public function runCompletionScan(): int
    {
        $recovered = 0;
        foreach (Survey::model()->findAllByAttributes(['active' => 'Y']) as $survey) {
            $this->safely(function () use ($survey, &$recovered) {
                $recovered += $this->scanSurvey((int) $survey->sid);
            });
        }
        return $recovered;
    }

    private function scanSurvey(int $surveyId): int
    {
        $scanner = new MjyCompletionScanner(App()->getDb(), $this->readyEventLog());
        return $scanner->scanSurvey($surveyId, $this->currentGeneration($surveyId));
    }

    /**
     * @param int|string|null $surveyId
     * @param CActiveRecord|null $model
     */
    private function recordSaved($surveyId, $model): void
    {
        if (empty($surveyId) || $model === null || empty($model->id)) {
            return;
        }
        $this->readyEventLog()->recordSaved(
            (int) $surveyId,
            $this->currentGeneration((int) $surveyId),
            (int) $model->id,
            MjyEventLog::SOURCE_HOOK
        );
    }

    /**
     * @param int|string|null $surveyId
     * @param CActiveRecord|null $model
     */
    private function recordDeleted($surveyId, $model): void
    {
        if (empty($surveyId) || $model === null || empty($model->id)) {
            return;
        }
        $this->readyEventLog()->recordDeleted(
            (int) $surveyId,
            $this->currentGeneration((int) $surveyId),
            (int) $model->id,
            MjyEventLog::SOURCE_HOOK
        );
    }

    /**
     * Hooks can fire before beforeActivate ever ran (e.g. plugin enabled by
     * direct DB change), so every writer makes sure the tables exist.
     */
    private function ensureSchemaOnce(): void
    {
        if (!$this->isSchemaReady) {
            $this->ensureSchema();
        }
    }

    private function readyEventLog(): MjyEventLog
    {
        $this->ensureSchemaOnce();
        return $this->eventLog();
    }

    private function transport(): ?MjyEventTransport
    {
        $endpoint = (string) getenv(self::EVENTS_URL_ENV);
        $secret = (string) getenv(self::EVENTS_SECRET_ENV);
        if ($endpoint === '' || $secret === '') {
            return null;
        }
        $clientCertificate = (string) getenv(self::EVENTS_CLIENT_CERT_ENV);
        return new MjyHttpEventTransport($endpoint, $secret, $clientCertificate === '' ? null : $clientCertificate);
    }

    private function eventLog(): MjyEventLog
    {
        if ($this->eventLog === null) {
            $this->eventLog = new MjyEventLog(App()->getDb(), self::engineInstanceId());
        }
        return $this->eventLog;
    }

    private function generations(): MjyGenerationStore
    {
        if ($this->generations === null) {
            $this->generations = new MjyGenerationStore(App()->getDb());
        }
        return $this->generations;
    }

    /**
     * Event log failures must never break the survey runtime or an admin
     * action; the scanner recovers anything missed here.
     */
    private function safely(callable $handler): void
    {
        try {
            $handler();
        } catch (\Throwable $exception) {
            Yii::log(
                sprintf('%s: %s', get_class($exception), $exception->getMessage()),
                CLogger::LEVEL_ERROR,
                self::LOG_CATEGORY
            );
        }
    }
}
