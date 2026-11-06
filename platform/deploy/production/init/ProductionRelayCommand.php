<?php

/** Invoke only the Survey event bridge; never dispatch global plugin cron. */
class ProductionRelayCommand extends CConsoleCommand
{
    public function actionRun()
    {
        $record = Plugin::model()->findByAttributes(array('name' => 'MjyPlatformBridge', 'active' => 1));
        if (!$record) {
            fwrite(STDERR, "event bridge is not active\n");
            return 1;
        }
        $plugin = Yii::app()->getPluginManager()->loadPlugin('MjyPlatformBridge', $record->id);
        if (!($plugin instanceof MjyPlatformBridge)) {
            fwrite(STDERR, "event bridge could not be loaded\n");
            return 1;
        }
        $failed = false;
        try {
            $plugin->runCompletionScan();
        } catch (Throwable $ignored) {
            fwrite(STDERR, "event reconciliation failed\n");
            $failed = true;
        }
        try {
            $plugin->relayEvents();
        } catch (Throwable $ignored) {
            fwrite(STDERR, "event relay failed\n");
            $failed = true;
        }
        return $failed ? 1 : 0;
    }
}
