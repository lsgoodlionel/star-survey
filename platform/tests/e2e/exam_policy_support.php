<?php

/**
 * P0-00.7 端到端策略验证的公共部件：作答者行为、HTTP、问卷装配、RemoteControl、
 * 运行环境。场景与断言在 platform/tests/e2e/exam_policy.php。
 *
 * 拆成两个文件只为了单文件不超过 800 行，没有其他含义。
 */

declare(strict_types=1);

const BASE_URL = 'http://localhost/index.php';
const ADMIN_USER = 'admin';
const ADMIN_PASSWORD = 'exam-secret';
const SURVEY_FIXTURE = 'tests/data/surveys/limesurvey_survey_376789_quotas.lss';
const COMPLETED_MARKER = 'completed-wrapper';
const QUOTA_ANSWER_CODE = 'AO01';
const QUOTA_QUESTION_TITLE = 'G02Q03';

// ------------------------------------------------------------ 作答者行为

/**
 * 完整走一遍：进场 → 提交。
 *
 * @param array<string, string> $extraFields 额外塞进 POST 的伪造字段
 * @return array{completed: bool, body: string}
 */
function runRespondent(array $survey, ?string $token, array $extraFields = []): array
{
    $session = enterSurvey($survey, $token);
    if (!$session['entered']) {
        cleanupSession($session);
        return ['completed' => false, 'body' => $session['body']];
    }
    $outcome = submitPreparedPage($survey, $session, $extraFields);
    cleanupSession($session);
    return $outcome;
}

/**
 * 进场：拿到作答页并解析出表单。返回的会话可以放很久再提交。
 *
 * @return array{jar: string, entered: bool, body: string, form: array|null}
 */
function enterSurvey(array $survey, ?string $token): array
{
    $jar = tempnam(sys_get_temp_dir(), 'exam-session');
    $page = httpRequest(surveyUrl($survey['sid'], $token), null, $jar);
    $form = parseSurveyForm($page['body']);
    return [
        'jar' => $jar,
        'entered' => $form !== null && in_array('movesubmit', $form['moves'], true),
        'body' => $page['body'],
        'form' => $form,
    ];
}

/**
 * 用进场时解析到的表单提交。
 *
 * @param array<string, string> $extraFields
 * @return array{completed: bool, body: string}
 */
function submitPreparedPage(array $survey, array $session, array $extraFields = []): array
{
    if ($session['form'] === null) {
        return ['completed' => false, 'body' => $session['body']];
    }
    $page = httpRequest($session['form']['action'], submitFields($survey, $session['form'], $extraFields), $session['jar']);
    return ['completed' => str_contains($page['body'], COMPLETED_MARKER), 'body' => $page['body']];
}

/**
 * @param array<string, string> $extraFields
 * @return array<string, string>
 */
function submitFields(array $survey, array $form, array $extraFields = []): array
{
    return $extraFields + [$survey['quotaField'] => QUOTA_ANSWER_CODE] + $form['fields'] + ['move' => 'movesubmit'];
}

/**
 * 顺序占掉 limit-1 个名额，留下最后一个给并发去抢。
 */
function fillQuotaUpToLastSlot(array $survey, int $count): void
{
    for ($index = 0; $index < $count; $index++) {
        $outcome = runRespondent($survey, null);
        if (!$outcome['completed']) {
            throw new RuntimeException("预热第 {$index} 份答卷失败：" . substr(strip_tags($outcome['body']), 0, 300));
        }
    }
}

/**
 * 预备 N 个已经进场、停在提交按钮前的会话。
 *
 * @return array<int, array{jar: string, entered: bool, body: string, form: array|null}>
 */
function prepareSessions(array $survey, int $count): array
{
    $requests = [];
    $jars = [];
    for ($index = 0; $index < $count; $index++) {
        $jars[$index] = tempnam(sys_get_temp_dir(), 'exam-prep');
        $requests[$index] = ['url' => surveyUrl($survey['sid'], null), 'post' => null, 'jar' => $jars[$index]];
    }
    $bodies = httpMulti($requests);

    $sessions = [];
    foreach ($bodies as $index => $body) {
        $form = parseSurveyForm($body);
        if ($form === null || !in_array('movesubmit', $form['moves'], true)) {
            cleanupSessions($sessions);
            throw new RuntimeException("第 {$index} 个并发会话没能进场：" . substr(strip_tags($body), 0, 300));
        }
        $sessions[] = ['jar' => $jars[$index], 'entered' => true, 'body' => $body, 'form' => $form];
    }
    return $sessions;
}

/**
 * 让所有预备好的会话同时提交。
 *
 * @param array<int, array{jar: string, form: array|null}> $sessions
 * @return string[] 每个会话拿到的响应体
 */
function fireConcurrently(array $survey, array $sessions): array
{
    $requests = [];
    foreach ($sessions as $session) {
        $requests[] = [
            'url' => $session['form']['action'],
            'post' => submitFields($survey, $session['form']),
            'jar' => $session['jar'],
        ];
    }
    return httpMulti($requests);
}

/**
 * @param array<int, array{jar: string}> $sessions
 */
function cleanupSessions(array $sessions): void
{
    foreach ($sessions as $session) {
        cleanupSession($session);
    }
}

/**
 * @param array{jar: string} $session
 */
function cleanupSession(array $session): void
{
    @unlink($session['jar']);
}

function surveyUrl(int $surveyId, ?string $token): string
{
    $query = ['lang' => 'en', 'newtest' => 'Y'];
    if ($token !== null) {
        $query['token'] = $token;
    }
    return BASE_URL . "/{$surveyId}?" . http_build_query($query);
}

function hasSurveyForm(string $html): bool
{
    $form = parseSurveyForm($html);
    return $form !== null && in_array('movesubmit', $form['moves'], true);
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

// ---------------------------------------------------------------- HTTP

/**
 * @return array{body: string, error: ?string}
 */
function httpRequest(string $url, ?array $postFields, string $cookieJar): array
{
    $curl = curl_init($url);
    curl_setopt_array($curl, curlOptions($cookieJar, $postFields));
    $body = curl_exec($curl);
    $error = $body === false ? curl_error($curl) : null;
    curl_close($curl);
    return ['body' => $body === false ? '' : $body, 'error' => $error];
}

/**
 * 同时发出全部请求：这是"最后一个名额归谁"唯一有意义的测法。
 *
 * @param array<int, array{url: string, post: ?array, jar: string}> $requests
 * @return string[] 与入参同序的响应体
 */
function httpMulti(array $requests): array
{
    $multi = curl_multi_init();
    $handles = [];
    foreach ($requests as $index => $request) {
        $curl = curl_init($request['url']);
        curl_setopt_array($curl, curlOptions($request['jar'], $request['post']));
        curl_multi_add_handle($multi, $curl);
        $handles[$index] = $curl;
    }
    do {
        $status = curl_multi_exec($multi, $running);
        if ($running > 0) {
            curl_multi_select($multi, 1.0);
        }
    } while ($running > 0 && $status === CURLM_OK);

    $bodies = [];
    foreach ($handles as $index => $curl) {
        $bodies[$index] = (string) curl_multi_getcontent($curl);
        curl_multi_remove_handle($multi, $curl);
        curl_close($curl);
    }
    curl_multi_close($multi);
    ksort($bodies);
    return $bodies;
}

/**
 * @return array<int, mixed>
 */
function curlOptions(string $cookieJar, ?array $postFields): array
{
    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_COOKIEJAR => $cookieJar,
        CURLOPT_COOKIEFILE => $cookieJar,
        CURLOPT_TIMEOUT => 180,
    ];
    if ($postFields !== null) {
        $options[CURLOPT_POST] = true;
        $options[CURLOPT_POSTFIELDS] = http_build_query($postFields);
    }
    return $options;
}

// ------------------------------------------------------------ 问卷装配

/**
 * 导入夹具问卷，调成单页、非必答，激活（必要时开启参与者表）。
 *
 * @return array{sid: int, quotaField: string, quotaQid: int}
 */
function createSurvey(string $title, bool $withTokens): array
{
    $key = rpc('get_session_key', [ADMIN_USER, ADMIN_PASSWORD]);
    if (!is_string($key)) {
        throw new RuntimeException('RemoteControl 登录失败：' . json_encode($key));
    }
    try {
        $lss = base64_encode((string) file_get_contents(SURVEY_FIXTURE));
        $surveyId = (int) assertRpcOk(rpc('import_survey', [$key, $lss, 'lss', null, $title]), 'import_survey');
        assertRpcOk(rpc('set_survey_properties', [$key, $surveyId, [
            'format' => 'A',          // 全部题目放在一页，作答流程只有一次提交
            'usecaptcha' => 'N',
            'datestamp' => 'Y',       // 需要 startdate/datestamp 列来验证时间戳来源
            'anonymized' => 'N',
            'alloweditaftercompletion' => 'N',
            'usecookie' => 'N',
        ]]), 'set_survey_properties');
        relaxMandatoryQuestions($surveyId);
        // 夹具自带三条配额，其中一条限额为 0（命中即终止）。端到端只想验证自己
        // 定义的那一条，先全部清掉。
        clearQuotas(connect(), $surveyId);
        assertRpcOk(rpc('activate_survey', [$key, $surveyId]), 'activate_survey');
        if ($withTokens) {
            assertRpcOk(rpc('activate_tokens', [$key, $surveyId]), 'activate_tokens');
        }
        $question = quotaQuestion($key, $surveyId);
        return [
            'sid' => $surveyId,
            'quotaQid' => $question['qid'],
            'quotaField' => $question['fieldname'],
        ];
    } finally {
        rpc('release_session_key', [$key]);
    }
}

/**
 * 夹具里有必答题，端到端只关心配额题，其余一律放开。
 */
function relaxMandatoryQuestions(int $surveyId): void
{
    $db = connect();
    $db->prepare('UPDATE ' . table('questions') . ' SET mandatory = ? WHERE sid = ?')->execute(['N', $surveyId]);
}

/**
 * 配额题的答卷表列名／表单字段名。LimeSurvey 7 里是 `Q<qid>`，不是历史上的
 * `<sid>X<gid>X<qid>`，所以只能从 get_fieldmap 回读，不能自己拼（ADR 0005）。
 *
 * @return array{qid: int, fieldname: string}
 */
function quotaQuestion(string $key, int $surveyId): array
{
    $fieldmap = rpc('get_fieldmap', [$key, $surveyId]);
    if (!is_array($fieldmap)) {
        throw new RuntimeException('get_fieldmap 失败：' . json_encode($fieldmap, JSON_UNESCAPED_UNICODE));
    }
    foreach ($fieldmap as $fieldname => $field) {
        if (($field['title'] ?? null) === QUOTA_QUESTION_TITLE && ($field['type'] ?? null) === 'L') {
            return ['qid' => (int) $field['qid'], 'fieldname' => (string) $fieldname];
        }
    }
    throw new RuntimeException("问卷 {$surveyId} 的 fieldmap 里找不到配额题 " . QUOTA_QUESTION_TITLE);
}

/**
 * 把夹具自带的配额换成一条我们自己的：限额 $limit，命中 QUOTA_ANSWER_CODE。
 */
function defineEngineQuota(PDO $db, array $survey, int $limit): void
{
    clearQuotas($db, $survey['sid']);
    $db->prepare('INSERT INTO ' . table('quota')
        . ' (sid, name, qlimit, action, active, autoload_url) VALUES (?, ?, ?, 1, 1, 0)')
        ->execute([$survey['sid'], 'p0-last-slot', $limit]);
    $quotaId = (int) $db->lastInsertId();
    $db->prepare('INSERT INTO ' . table('quota_members') . ' (sid, qid, quota_id, code) VALUES (?, ?, ?, ?)')
        ->execute([$survey['sid'], $survey['quotaQid'], $quotaId, QUOTA_ANSWER_CODE]);
    $db->prepare('INSERT INTO ' . table('quota_languagesettings')
        . ' (quotals_quota_id, quotals_message, quotals_name, quotals_url, quotals_urldescrip, quotals_language)'
        . ' VALUES (?, ?, ?, ?, ?, ?)')
        ->execute([$quotaId, 'quota reached', 'p0-last-slot', '', '', 'en']);
}

/**
 * 清掉一份问卷上的所有配额定义。
 */
function clearQuotas(PDO $db, int $surveyId): void
{
    $db->prepare('DELETE FROM ' . table('quota_members') . ' WHERE sid = ?')->execute([$surveyId]);
    $db->prepare('DELETE FROM ' . table('quota_languagesettings')
        . ' WHERE quotals_quota_id IN (SELECT id FROM ' . table('quota') . ' WHERE sid = ?)')->execute([$surveyId]);
    $db->prepare('DELETE FROM ' . table('quota') . ' WHERE sid = ?')->execute([$surveyId]);
}

function addToken(int $surveyId): string
{
    $key = rpc('get_session_key', [ADMIN_USER, ADMIN_PASSWORD]);
    try {
        $token = 'EXAM' . strtoupper(bin2hex(random_bytes(6)));
        $participant = [['email' => "{$token}@exam.invalid", 'lastname' => 'Exam', 'firstname' => 'Candidate', 'token' => $token]];
        assertRpcOk(rpc('add_participants', [$key, $surveyId, $participant, false]), 'add_participants');
        return $token;
    } finally {
        rpc('release_session_key', [$key]);
    }
}

function setSurveyExpiry(PDO $db, int $surveyId, ?string $expires): void
{
    $db->prepare('UPDATE ' . table('surveys') . ' SET expires = ? WHERE sid = ?')->execute([$expires, $surveyId]);
}

/**
 * 模拟"平台下发运行时策略"。正式版走平台网关调用 MjyRuntimePolicy::applyPolicy()，
 * 这里直接写同一批表，避免端到端脚本去引导整个 Yii 应用。
 */
function applyPolicy(PDO $db, int $surveyId, int $durationSeconds, int $slotLimit, int $leaseTtlSeconds): void
{
    $now = gmdate('Y-m-d H:i:s');
    $db->prepare('DELETE FROM ' . table('mjyruntimepolicy_survey_policy') . ' WHERE survey_id = ?')->execute([$surveyId]);
    $db->prepare('INSERT INTO ' . table('mjyruntimepolicy_survey_policy')
        . ' (survey_id, exam_duration_seconds, quota_slot_limit, lease_ttl_seconds, updated_at) VALUES (?, ?, ?, ?, ?)')
        ->execute([$surveyId, $durationSeconds, $slotLimit, $leaseTtlSeconds, $now]);
    if ($slotLimit <= 0) {
        return;
    }
    $quotaKey = "survey:{$surveyId}";
    $db->prepare('DELETE FROM ' . table('mjyruntimepolicy_quota') . ' WHERE quota_key = ?')->execute([$quotaKey]);
    $db->prepare('INSERT INTO ' . table('mjyruntimepolicy_quota')
        . ' (quota_key, survey_id, slot_limit, updated_at) VALUES (?, ?, ?, ?)')
        ->execute([$quotaKey, $surveyId, $slotLimit, $now]);
}

// ---------------------------------------------------------- RemoteControl

/**
 * @param mixed $result
 * @return mixed
 */
function assertRpcOk($result, string $method)
{
    if (is_array($result) && isset($result['status']) && !in_array($result['status'], ['OK', 'success'], true)) {
        throw new RuntimeException("{$method} 失败：" . json_encode($result, JSON_UNESCAPED_UNICODE));
    }
    return $result;
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
        CURLOPT_TIMEOUT => 180,
    ]);
    $body = curl_exec($curl);
    curl_close($curl);
    $decoded = json_decode((string) $body, true);
    if (!is_array($decoded) || !array_key_exists('result', $decoded)) {
        throw new RuntimeException("RemoteControl {$method} 返回：" . substr((string) $body, 0, 300));
    }
    return $decoded['result'];
}

// ------------------------------------------------------------ 运行环境

function connect(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }
    if (!defined('BASEPATH')) {
        define('BASEPATH', getcwd() . '/'); // config.php 没有它会拒绝被直接引入
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

function runCron(): void
{
    exec('php application/commands/console.php plugin cron 2>&1', $output, $exitCode);
    if ($exitCode !== 0) {
        throw new RuntimeException("plugin cron 失败：\n" . implode("\n", $output));
    }
}
