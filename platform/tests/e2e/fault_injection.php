<?php

/**
 * P0-00.4 end-to-end fault injection against the real survey runtime.
 *
 * Runs inside survey-test-web (see run-fault-injection.sh). Creates a
 * one-question survey through RemoteControl, fills it over HTTP like a
 * respondent, and kills the PHP worker at chosen engine events via the
 * test-only FaultInjector plugin. Asserts what the event log contains before
 * and after the MjyPlatformBridge cron scan.
 *
 * Exit code 0 when every scenario passes.
 */

declare(strict_types=1);

const BASE_URL = 'http://localhost/index.php';
const ADMIN_USER = 'admin';
const ADMIN_PASSWORD = 'password';
const QUESTION_FIXTURE = 'tests/data/surveys/limesurvey_question_import_question_test.lsq';
const FAULT_DIR = 'tmp/mjy-fault';
const MAX_PAGES = 6;
const COMPLETED_MARKER = 'completed-wrapper'; // the completion page still contains form#limesurvey
const BRIDGE_PRIORITY = 0;
const FAULT_PRIORITY = 100; // loaded (and dispatched) before the bridge

main();

function main(): void
{
    $db = connect();
    enableRemoteControl($db);
    enablePlugin($db, 'MjyPlatformBridge', BRIDGE_PRIORITY);
    enablePlugin($db, 'FaultInjector', FAULT_PRIORITY);
    clearFaults();

    $surveyId = createSurvey();
    info("survey $surveyId created and activated");

    $results = [
        scenarioNoFault($db, $surveyId),
        scenarioCrashBeforeCompletionHook($db, $surveyId),
        scenarioCrashBeforeSubmitdate($db, $surveyId),
    ];
    clearFaults();

    $failed = array_filter($results, static function (array $result): bool {
        return !$result['passed'];
    });
    echo json_encode(['surveyId' => $surveyId, 'scenarios' => $results], JSON_PRETTY_PRINT), "\n";
    exit(count($failed) === 0 ? 0 : 1);
}

/**
 * Baseline: the completion hook records the fact directly.
 */
function scenarioNoFault(PDO $db, int $surveyId): array
{
    $outcome = completeSurvey($surveyId);
    $responseId = latestResponseId($db, $surveyId);
    $checks = [
        'request succeeded' => $outcome['completed'],
        'submitdate written' => isSubmitted($db, $surveyId, $responseId),
        'one completion from hook' => completions($db, $surveyId, $responseId) === ['hook'],
    ];
    return result('no fault', $responseId, $checks);
}

/**
 * Worker dies after submitdate is written, before afterSurveyComplete reaches
 * the bridge: the cron scan must recover the completion exactly once.
 */
function scenarioCrashBeforeCompletionHook(PDO $db, int $surveyId): array
{
    setFault('afterSurveyComplete');
    $outcome = completeSurvey($surveyId);
    clearFaults();
    $responseId = latestResponseId($db, $surveyId);

    $isLostBeforeScan = completions($db, $surveyId, $responseId) === [];
    $scanStartedAt = microtime(true);
    runCron();
    runCron();
    $scanSeconds = microtime(true) - $scanStartedAt;

    $checks = [
        'worker was killed' => $outcome['crashed'],
        'submitdate written' => isSubmitted($db, $surveyId, $responseId),
        'completion missing before scan' => $isLostBeforeScan,
        'one completion from scanner after two scans' => completions($db, $surveyId, $responseId) === ['scanner'],
    ];
    $result = result('crash before completion hook', $responseId, $checks);
    $result['twoScansSeconds'] = round($scanSeconds, 2);
    return $result;
}

/**
 * Worker dies in afterResponseSave on the final page, before submitdate is
 * written: the response is not complete, so no completion may appear.
 */
function scenarioCrashBeforeSubmitdate(PDO $db, int $surveyId): array
{
    $outcome = completeSurvey($surveyId, 'afterResponseSave');
    clearFaults();
    $responseId = latestResponseId($db, $surveyId);
    runCron();

    $checks = [
        'worker was killed' => $outcome['crashed'],
        'submitdate not written' => !isSubmitted($db, $surveyId, $responseId),
        'no completion after scan' => completions($db, $surveyId, $responseId) === [],
    ];
    return result('crash before submitdate', $responseId, $checks);
}

// ---------------------------------------------------------------- respondent

/**
 * Walks the survey like a browser. When $faultOnSubmit is set, the fault is
 * armed just before the final (movesubmit) POST.
 *
 * @return array{completed: bool, crashed: bool}
 */
function completeSurvey(int $surveyId, ?string $faultOnSubmit = null): array
{
    $cookieJar = tempnam(sys_get_temp_dir(), 'e2e-cookies');
    try {
        $page = httpRequest(BASE_URL . "/$surveyId?lang=en&newtest=Y", null, $cookieJar);
        for ($step = 0; $step < MAX_PAGES; $step++) {
            if ($page['error'] !== null) {
                return ['completed' => false, 'crashed' => true];
            }
            if (strpos($page['body'], COMPLETED_MARKER) !== false) {
                return ['completed' => true, 'crashed' => false];
            }
            $form = parseSurveyForm($page['body']);
            $isSubmit = $form !== null && in_array('movesubmit', $form['moves'], true);
            $isNext = $form !== null && in_array('movenext', $form['moves'], true);
            if (!$isSubmit && !$isNext) {
                throw new RuntimeException("Survey $surveyId: page has no next/submit button:\n" . substr($page['body'], 0, 500));
            }
            if ($isSubmit && $faultOnSubmit !== null) {
                setFault($faultOnSubmit);
            }
            $fields = $form['fields'] + ['move' => $isSubmit ? 'movesubmit' : 'movenext'];
            $page = httpRequest($form['action'], $fields, $cookieJar);
        }
        throw new RuntimeException("Survey $surveyId did not finish within " . MAX_PAGES . ' pages');
    } finally {
        @unlink($cookieJar);
    }
}

/**
 * @return array{action: string, fields: array<string, string>, moves: string[]}|null
 */
function parseSurveyForm(string $html): ?array
{
    $document = new DOMDocument();
    libxml_use_internal_errors(true);
    $document->loadHTML($html);
    libxml_clear_errors();
    $form = $document->getElementById('limesurvey');
    if ($form === null) {
        return null;
    }
    $fields = [];
    $moves = [];
    foreach ($form->getElementsByTagName('input') as $input) {
        if ($input->getAttribute('type') === 'hidden' && $input->getAttribute('name') !== '') {
            $fields[$input->getAttribute('name')] = $input->getAttribute('value');
        }
    }
    foreach ($form->getElementsByTagName('textarea') as $textarea) {
        $fields[$textarea->getAttribute('name')] = 'e2e answer';
    }
    foreach ($form->getElementsByTagName('button') as $button) {
        if ($button->getAttribute('name') === 'move') {
            $moves[] = $button->getAttribute('value');
        }
    }
    $action = $form->getAttribute('action');
    if (strpos($action, 'http') !== 0) {
        $action = 'http://localhost' . $action;
    }
    return ['action' => $action, 'fields' => $fields, 'moves' => $moves];
}

/**
 * @return array{body: string, error: ?string}
 */
function httpRequest(string $url, ?array $postFields, string $cookieJar): array
{
    $curl = curl_init($url);
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_COOKIEJAR => $cookieJar,
        CURLOPT_COOKIEFILE => $cookieJar,
        CURLOPT_TIMEOUT => 60,
    ]);
    if ($postFields !== null) {
        curl_setopt($curl, CURLOPT_POST, true);
        curl_setopt($curl, CURLOPT_POSTFIELDS, http_build_query($postFields));
    }
    $body = curl_exec($curl);
    $error = $body === false ? curl_error($curl) : null;
    curl_close($curl);
    return ['body' => $body === false ? '' : $body, 'error' => $error];
}

// ---------------------------------------------------------- remote control

function createSurvey(): int
{
    $key = rpc('get_session_key', [ADMIN_USER, ADMIN_PASSWORD]);
    if (!is_string($key)) {
        throw new RuntimeException('RemoteControl login failed: ' . json_encode($key));
    }
    try {
        $surveyId = rpc('add_survey', [$key, 0, 'P0 fault injection ' . gmdate('c'), 'en', 'A']);
        assertRpcOk($surveyId, 'add_survey');
        $groupId = rpc('add_group', [$key, $surveyId, 'G1', '']);
        assertRpcOk($groupId, 'add_group');
        $lsq = base64_encode((string) file_get_contents(QUESTION_FIXTURE));
        assertRpcOk(rpc('import_question', [$key, $surveyId, $groupId, $lsq, 'lsq', 'N']), 'import_question');
        assertRpcOk(rpc('set_survey_properties', [$key, $surveyId, ['usecaptcha' => 'N']]), 'set_survey_properties');
        assertRpcOk(rpc('activate_survey', [$key, $surveyId]), 'activate_survey');
        return (int) $surveyId;
    } finally {
        rpc('release_session_key', [$key]);
    }
}

/**
 * @param mixed $result
 */
function assertRpcOk($result, string $method): void
{
    if (is_array($result) && isset($result['status']) && !in_array($result['status'], ['OK', 'success'], true)) {
        throw new RuntimeException("$method failed: " . json_encode($result));
    }
}

/**
 * @return mixed
 */
function rpc(string $method, array $params)
{
    $curl = curl_init(BASE_URL . '/admin/remotecontrol');
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS => json_encode(['method' => $method, 'params' => $params, 'id' => 1]),
        CURLOPT_TIMEOUT => 120,
    ]);
    $body = curl_exec($curl);
    curl_close($curl);
    $decoded = json_decode((string) $body, true);
    if (!is_array($decoded) || !array_key_exists('result', $decoded)) {
        throw new RuntimeException("RemoteControl $method returned: " . substr((string) $body, 0, 300));
    }
    return $decoded['result'];
}

// ------------------------------------------------------------ environment

function connect(): PDO
{
    if (!defined('BASEPATH')) {
        define('BASEPATH', getcwd() . '/'); // config.php refuses direct access without it
    }
    $config = require 'application/config/config.php';
    $dbConfig = $config['components']['db'];
    $pdo = new PDO($dbConfig['connectionString'], $dbConfig['username'], $dbConfig['password']);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $GLOBALS['tablePrefix'] = $dbConfig['tablePrefix'];
    return $pdo;
}

function table(string $name): string
{
    return $GLOBALS['tablePrefix'] . $name;
}

function enableRemoteControl(PDO $db): void
{
    $db->prepare('DELETE FROM ' . table('settings_global') . ' WHERE stg_name = ?')->execute(['RPCInterface']);
    $db->prepare('INSERT INTO ' . table('settings_global') . ' (stg_name, stg_value) VALUES (?, ?)')
        ->execute(['RPCInterface', 'json']);
}

function enablePlugin(PDO $db, string $name, int $priority): void
{
    $plugins = table('plugins');
    $db->prepare("DELETE FROM $plugins WHERE name = ?")->execute([$name]);
    $db->prepare("INSERT INTO $plugins (name, plugin_type, active, priority, version, load_error) VALUES (?, 'user', 1, ?, '0.1.0', 0)")
        ->execute([$name, $priority]);
}

function setFault(string $eventName): void
{
    if (!is_dir(FAULT_DIR)) {
        mkdir(FAULT_DIR, 0777, true);
    }
    touch(FAULT_DIR . '/' . $eventName);
}

function clearFaults(): void
{
    foreach (glob(FAULT_DIR . '/*') ?: [] as $marker) {
        unlink($marker);
    }
}

function runCron(): void
{
    exec('php application/commands/console.php plugin cron 2>&1', $output, $exitCode);
    if ($exitCode !== 0) {
        throw new RuntimeException("plugin cron failed:\n" . implode("\n", $output));
    }
}

function latestResponseId(PDO $db, int $surveyId): int
{
    return (int) $db->query('SELECT MAX(id) FROM ' . table("responses_$surveyId"))->fetchColumn();
}

function isSubmitted(PDO $db, int $surveyId, int $responseId): bool
{
    $statement = $db->prepare('SELECT submitdate FROM ' . table("responses_$surveyId") . ' WHERE id = ?');
    $statement->execute([$responseId]);
    return $statement->fetchColumn() !== null;
}

/**
 * @return string[] sources of completion events for the response
 */
function completions(PDO $db, int $surveyId, int $responseId): array
{
    $statement = $db->prepare(
        'SELECT source FROM ' . table('mjyplatformbridge_event_log')
        . " WHERE event_type = 'response.completed' AND survey_id = ? AND response_id = ? ORDER BY id"
    );
    $statement->execute([$surveyId, $responseId]);
    return $statement->fetchAll(PDO::FETCH_COLUMN);
}

/**
 * @param array<string, bool> $checks
 */
function result(string $scenario, int $responseId, array $checks): array
{
    $passed = !in_array(false, $checks, true);
    info(($passed ? 'PASS ' : 'FAIL ') . $scenario);
    return ['scenario' => $scenario, 'responseId' => $responseId, 'passed' => $passed, 'checks' => $checks];
}

function info(string $message): void
{
    fwrite(STDERR, "[e2e] $message\n");
}
