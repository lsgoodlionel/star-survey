<?php
/** TEST ONLY: read-only checks; no application bootstrap, values or file bytes are emitted. */

function attachmentProbeInput(array $query): bool
{
    $patterns = ['sid'=>'/\A[1-9][0-9]{0,9}\z/D', 'responseId'=>'/\A[1-9][0-9]{0,9}\z/D',
        'generation'=>'/\A[A-Za-z0-9][A-Za-z0-9._-]{0,35}\z/D',
        'field'=>'/\A[A-Za-z0-9_#]{1,64}\z/D', 'storedName'=>'/\A[A-Za-z0-9][A-Za-z0-9._-]{0,254}\z/D'];
    if (count($query) !== count($patterns)) return false;
    foreach ($patterns as $key=>$pattern) {
        if (!isset($query[$key]) || !is_string($query[$key]) || preg_match($pattern, $query[$key]) !== 1) return false;
    }
    return strpos($query['storedName'], '..') === false
        && !in_array(strtolower($query['field']), ['token','id','submitdate','startlanguage','seed'], true);
}

/** Isolate only probe-generated Yii SQL/exception logs; preserve the original request logger. */
function attachmentProbeSilently(callable $read): array
{
    $logger = Yii::getLogger();
    Yii::setLogger(new class extends CLogger {
        public function log($message, $level = 'info', $category = 'application') {}
    });
    try { return $read(); } finally { Yii::setLogger($logger); }
}

function attachmentProbeReadOnly($db, callable $read): array
{
    if ($db->getCurrentTransaction() !== null || !in_array($db->getDriverName(), ['mysql','pgsql'], true)) {
        return ['stage'=>'evidence_unavailable'];
    }
    $transaction = null;
    $schemaCache = $db->schemaCachingDuration;
    $queryCache = $db->queryCachingDuration;
    $result = ['stage'=>'probe_failed'];
    try {
        $db->schemaCachingDuration = 0;
        $db->queryCachingDuration = 0;
        // MySQL requires the next-transaction characteristic before BEGIN; PostgreSQL after BEGIN.
        if ($db->getDriverName() === 'mysql') $db->createCommand('SET TRANSACTION READ ONLY')->execute();
        $transaction = $db->beginTransaction();
        if ($db->getDriverName() === 'pgsql') $db->createCommand('SET TRANSACTION READ ONLY')->execute();
        $result = $read();
    } catch (Throwable $error) {
        $result = ['stage'=>'probe_failed'];
    } finally {
        if ($transaction !== null) {
            try { $transaction->rollback(); } catch (Throwable $error) { $result = ['stage'=>'probe_failed']; }
        }
        $db->schemaCachingDuration = $schemaCache;
        $db->queryCachingDuration = $queryCache;
    }
    return $result;
}

function attachmentProbeChecks($db, array $query, string $uploadDir): array
{
    if (!attachmentProbeInput($query) || $uploadDir === '') return ['stage'=>'evidence_unavailable'];
    $e = ['stage'=>'generation_missing', 'generationEvaluated'=>true, 'generationTablePresent'=>false,
        'generationRowPresent'=>false, 'generationMatches'=>false, 'ownershipEvaluated'=>false,
        'tablePresent'=>false, 'fieldPresent'=>false, 'responsePresent'=>false, 'answerListsStoredName'=>false,
        'pathEvaluated'=>false, 'baseResolved'=>false, 'fileResolved'=>false, 'withinSurveyDirectory'=>false,
        'regularFile'=>false];
    $generationTable = $db->tablePrefix . 'mjyplatformbridge_generation';
    $e['generationTablePresent'] = $db->getSchema()->getTable($generationTable, true) !== null;
    if (!$e['generationTablePresent']) return $e;
    $generation = $db->createCommand()->select('generation')->from($generationTable)
        ->where('survey_id = :sid', [':sid'=>(int)$query['sid']])->queryScalar();
    $e['generationRowPresent'] = $generation !== false && $generation !== null;
    if (!$e['generationRowPresent']) return $e;
    $e['generationMatches'] = (string)$generation === $query['generation'];
    $e['stage'] = 'generation_mismatch';
    if (!$e['generationMatches']) return $e;

    $e['ownershipEvaluated'] = true;
    $table = $db->tablePrefix . 'survey_' . (int)$query['sid'];
    $schema = $db->getSchema()->getTable($table, true);
    $e['tablePresent'] = $schema !== null;
    $e['stage'] = 'table_missing';
    if (!$e['tablePresent']) return $e;
    $e['fieldPresent'] = isset($schema->columns[$query['field']]);
    $e['stage'] = 'field_missing';
    if (!$e['fieldPresent']) return $e;
    // Same actual-column whitelist and binding as MjyAttachmentLocator::answerColumn.
    $row = $db->createCommand()->select($query['field'])->from($table)
        ->where('id = :id', [':id'=>(int)$query['responseId']])->queryRow();
    $e['responsePresent'] = $row !== false;
    $e['stage'] = 'response_missing';
    if (!$e['responsePresent']) return $e;
    $decoded = json_decode((string)($row[$query['field']] ?? ''), true);
    foreach (is_array($decoded) ? $decoded : [] as $file) {
        if (is_array($file) && isset($file['filename']) && is_string($file['filename'])
            && $file['filename'] === $query['storedName']) $e['answerListsStoredName'] = true;
    }
    $e['stage'] = 'ownership_mismatch';
    if (!$e['answerListsStoredName']) return $e;

    $e['pathEvaluated'] = true;
    $base = realpath($uploadDir . '/surveys/' . (int)$query['sid'] . '/files');
    $e['baseResolved'] = $base !== false;
    $e['stage'] = 'base_unresolved';
    if (!$e['baseResolved']) return $e;
    $path = realpath($base . '/' . $query['storedName']);
    $e['fileResolved'] = $path !== false;
    $e['stage'] = 'file_unresolved';
    if (!$e['fileResolved']) return $e;
    $e['withinSurveyDirectory'] = strpos($path, $base . DIRECTORY_SEPARATOR) === 0;
    $e['stage'] = 'path_rejected';
    if (!$e['withinSurveyDirectory']) return $e;
    $e['regularFile'] = is_file($path);
    $e['stage'] = $e['regularFile'] ? 'locator_checks_pass' : 'not_regular_file';
    return $e;
}
