<?php

/**
 * TEST ONLY fault injection (P0-00.4). Mounted exclusively into the
 * survey-test-web container by docker-compose.dev.yml; never install it in a
 * real instance.
 *
 * When the marker file tmp/mjy-fault/<eventName> exists, the handler for that
 * event SIGKILLs the current PHP worker, simulating a crash at that point of
 * the request. Loaded with a higher priority than MjyPlatformBridge, so it
 * runs first and the bridge never sees the event.
 */
class FaultInjector extends \LimeSurvey\PluginManager\PluginBase
{
    private const MARKER_DIR = 'mjy-fault';
    private const SIGNAL_KILL = 9; // SIGKILL; the pcntl constant is not available here
    private const INJECTABLE_EVENTS = ['afterSurveyComplete', 'afterResponseSave'];

    protected static $description = 'TEST ONLY: crash the worker at a chosen event';
    protected static $name = 'FaultInjector';

    /** @var string[] */
    public $allowedPublicMethods = [];

    public function init()
    {
        foreach (self::INJECTABLE_EVENTS as $eventName) {
            $this->subscribe($eventName, 'maybeCrash');
        }
    }

    public function maybeCrash()
    {
        $eventName = $this->getEvent()->getEventName();
        $marker = Yii::app()->getConfig('tempdir') . DIRECTORY_SEPARATOR . self::MARKER_DIR
            . DIRECTORY_SEPARATOR . $eventName;
        if (file_exists($marker)) {
            posix_kill(posix_getpid(), self::SIGNAL_KILL);
        }
    }
}
