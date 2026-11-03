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
            $id = (string) getenv('SMOKE_SURVEY_ID');
            if (!preg_match('/^[1-9][0-9]{0,9}$/', $id) || (int) $id > 2147483647) {
                throw new RuntimeException('invalid diagnostic scope');
            }
            $sid = (int) $id;
            $db = Yii::app()->getDb();
            $table = $db->quoteTableName($db->tablePrefix . 'mjyplatformbridge_event_log');
            $report['recordedEvents'] = (int) $db->createCommand('SELECT COUNT(*) FROM ' . $table . ' WHERE survey_id = :sid')->bindValue(':sid', $sid)->queryScalar();
            $report['pendingEvents'] = (int) $db->createCommand('SELECT COUNT(*) FROM ' . $table . ' WHERE survey_id = :sid AND delivered_at IS NULL')->bindValue(':sid', $sid)->queryScalar();
            $report['deliveredEvents'] = (int) $db->createCommand('SELECT COUNT(*) FROM ' . $table . ' WHERE survey_id = :sid AND delivered_at IS NOT NULL')->bindValue(':sid', $sid)->queryScalar();
            $responseTable = $db->tablePrefix . 'responses_' . $sid;
            if ($db->getSchema()->getTable($responseTable) === null) {
                throw new RuntimeException('response table unavailable');
            }
            $report['completedResponses'] = (int) $db->createCommand('SELECT COUNT(*) FROM '
                . $db->quoteTableName($responseTable) . ' WHERE submitdate IS NOT NULL')->queryScalar();
            $report['phase'] = 'completed';
        } catch (Throwable $exception) {
            $class = get_class($exception);
            $report['errorCategory'] = in_array($class, array('RuntimeException', 'CDbException', 'CException'), true) ? $class : 'other';
        }
        echo 'SMOKE_BRIDGE_JSON:' . json_encode($report) . "\n";
        return 0;
    }
}
