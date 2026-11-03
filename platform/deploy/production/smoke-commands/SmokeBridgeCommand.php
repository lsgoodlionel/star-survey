<?php
/** Source-smoke only: counts and fixed classes, never event/response contents. */
if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
class SmokeBridgeCommand extends CConsoleCommand
{
    public function actionIndex()
    {
        $report = array('completedResponses' => 0, 'recordedEvents' => 0, 'pendingEvents' => 0,
            'deliveredEvents' => 0, 'phase' => 'inspect', 'errorCategory' => null, 'httpStatus' => null);
        try {
            $db = Yii::app()->getDb();
            $table = $db->quoteTableName($db->tablePrefix . 'mjyplatformbridge_event_log');
            $report['recordedEvents'] = (int) $db->createCommand('SELECT COUNT(*) FROM ' . $table)->queryScalar();
            $report['pendingEvents'] = (int) $db->createCommand('SELECT COUNT(*) FROM ' . $table . ' WHERE delivered_at IS NULL')->queryScalar();
            $report['deliveredEvents'] = (int) $db->createCommand('SELECT COUNT(*) FROM ' . $table . ' WHERE delivered_at IS NOT NULL')->queryScalar();
            $surveys = Survey::model()->findAllByAttributes(array('active' => 'Y'));
            foreach ($surveys as $survey) {
                $responseTable = $db->tablePrefix . 'survey_' . (int) $survey->sid;
                if ($db->getSchema()->getTable($responseTable) !== null) {
                    $report['completedResponses'] += (int) $db->createCommand('SELECT COUNT(*) FROM '
                        . $db->quoteTableName($responseTable) . ' WHERE submitdate IS NOT NULL')->queryScalar();
                }
            }
            // Read only the existing bounded tail, never retry delivery after
            // the product probe has archived its disposable project.
            $path = Yii::app()->runtimePath . '/application.log';
            if (is_file($path) && !is_link($path)) {
                $handle = fopen($path, 'rb');
                if ($handle !== false) {
                    fseek($handle, max(0, filesize($path) - 1048576));
                    $tail = fread($handle, 1048576);
                    fclose($handle);
                    if (is_string($tail) && preg_match_all('/Event delivery rejected with HTTP ([1-5][0-9]{2}):/', $tail, $matches)) {
                        $report['httpStatus'] = (int) end($matches[1]);
                        $report['errorCategory'] = 'relay-rejected';
                    }
                }
            }
            $report['phase'] = 'completed';
        } catch (Throwable $exception) {
            $class = get_class($exception);
            $report['errorCategory'] = in_array($class, array('RuntimeException', 'CDbException', 'CException'), true) ? $class : 'other';
        }
        echo json_encode($report) . "\n";
        return 0;
    }
}
