<?php

/**
 * P0-00.3 题型纵切端到端的支撑代码：HTTP 作答者、RemoteControl、数据库读写、环境引导。
 *
 * 场景与断言在 question_slice.php 里，这里只放可复用的机械部分。
 */

declare(strict_types=1);

const BASE_URL = 'http://localhost/index.php';
const ADMIN_USER = 'admin';
const ADMIN_PASSWORD = 'password';
const SURVEY_FIXTURE = 'platform/tests/fixtures/surveys/mjy-question-slice.lss';
const THEME_DIR = 'themes/question/mjy-repeating-table/survey/questions/answer/longfreetext';
const THEME_NAME = 'mjy-repeating-table';
const BRIDGE_PLUGIN = 'MjyPlatformBridge';
const QUESTIONS_PLUGIN = 'MjyQuestionExtensions';
const MAX_PAGES = 8;
const COMPLETED_MARKER = 'completed-wrapper';
const RPC_TIMEOUT_SECONDS = 180;

const UPLOAD_FILE_NAME = 'recording.txt';
const UPLOAD_FILE_BODY = 'MJY P0-00.3 fake recording payload';

// ------------------------------------------------------------ 作答者（HTTP）

/**
 * 每个作答者一份独立的 cookie 罐。
 *
 * @return array{jar: string}
 */
function startResponse(): array
{
    return ['jar' => (string) tempnam(sys_get_temp_dir(), 'e2e-q')];
}

/**
 * @param array{jar: string} $session
 */
function closeSession(array $session): void
{
    @unlink($session['jar']);
}

/**
 * @param array{jar: string} $session
 */
function openSurvey(array $session, int $surveyId): string
{
    $page = httpRequest(BASE_URL . "/$surveyId?lang=en&newtest=Y", null, $session['jar']);
    return $page['body'];
}

/**
 * 从进入问卷一直走到提交。
 *
 * @param array{jar: string} $session
 * @param array<string, mixed> $map
 * @param array<string, mixed> $answers
 * @return array{completed: bool, body: string}
 */
function walkSurvey(array $session, int $surveyId, array $map, array $answers): array
{
    return continueSurvey($session, openSurvey($session, $surveyId), $map, $answers);
}

/**
 * 从当前页继续走到提交。
 *
 * @param array{jar: string} $session
 * @param array<string, mixed> $map
 * @param array<string, mixed> $answers
 * @return array{completed: bool, body: string}
 */
function continueSurvey(array $session, string $body, array $map, array $answers): array
{
    for ($step = 0; $step < MAX_PAGES; $step++) {
        if (strpos($body, COMPLETED_MARKER) !== false) {
            return ['completed' => true, 'body' => $body];
        }
        $form = parseSurveyForm($body);
        if ($form === null) {
            return ['completed' => false, 'body' => $body];
        }
        $move = in_array('movesubmit', $form['moves'], true) ? 'movesubmit' : 'movenext';
        $fields = fillFields($body, $map, $answers, $session);
        $body = submitPage($session, $body, $fields, $move);
    }
    return ['completed' => false, 'body' => $body];
}

/**
 * 把这一页上出现的题目填上。字段名来自 get_fieldmap 的映射，不靠猜。
 *
 * @param array<string, mixed> $map
 * @param array<string, mixed> $answers
 * @param array{jar: string}|null $session
 * @return array<string, string>
 */
function fillFields(string $body, array $map, array $answers, ?array $session = null): array
{
    $fields = [];
    foreach ($answers['matrix'] ?? [] as $subCode => $code) {
        $field = $map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions'][$subCode];
        if (strpos($body, $field) !== false) {
            $fields[$field] = $code;
        }
    }
    if (isset($answers['table']) && strpos($body, $map['QTABLE']['field']) !== false) {
        $fields[$map['QTABLE']['field']] = $answers['table'];
    }
    if (!empty($answers['upload']) && $session !== null && strpos($body, $map['QUPLOAD']['field']) !== false) {
        $form = parseSurveyForm($body);
        $csrfToken = $form === null ? '' : ($form['fields']['YII_CSRF_TOKEN'] ?? '');
        $uploaded = uploadFile($session, $map['QUPLOAD']['field'], $csrfToken);
        $fields[$map['QUPLOAD']['field']] = json_encode([$uploaded], JSON_UNESCAPED_UNICODE);
        $fields[$map['QUPLOAD']['field'] . '_Cfilecount'] = '1';
    }
    return $fields;
}

/**
 * @param array{jar: string} $session
 * @param array<string, string> $fields
 */
function submitPage(array $session, string $body, array $fields, string $move): string
{
    $form = parseSurveyForm($body);
    if ($form === null) {
        throw new RuntimeException("页面上没有问卷表单：\n" . substr($body, 0, 500));
    }
    // 引擎会把上传题的值列与计数列渲染成隐藏 input，必须让我们填的值覆盖它们，
    // 所以用 array_merge 而不是 "+"（"+" 保留左侧的已有键）。
    $post = array_merge($form['fields'], $fields, ['move' => $move]);
    $page = httpRequest($form['action'], $post, $session['jar']);
    return $page['body'];
}

/**
 * 只提交自增表格这一页，返回是否成功前进。
 *
 * @param array<string, mixed> $map
 * @return array{advanced: bool, body: string}
 */
function attemptTableAnswer(int $surveyId, array $map, string $answer): array
{
    $session = startResponse();
    try {
        $body = openSurvey($session, $surveyId);
        $body = submitPage($session, $body, [], 'movenext'); // 跳过数组题（非必答）
        $before = currentStep($body);
        $body = submitPage($session, $body, [$map['QTABLE']['field'] => $answer], 'movenext');
        return ['advanced' => currentStep($body) > $before, 'body' => $body];
    } finally {
        closeSession($session);
    }
}

/**
 * 表单里的 thisstep 隐藏字段就是当前步号。
 */
function currentStep(string $body): int
{
    $form = parseSurveyForm($body);
    return $form === null ? -1 : (int) ($form['fields']['thisstep'] ?? -1);
}

/**
 * 「稍后继续」分两步：先 POST saveall 拿到保存表单（引擎把它嵌在主表单里，
 * 其隐藏字段含 savesubmit=save），再 POST 名字与口令。
 *
 * @param array{jar: string} $session
 */
function saveAndResumeLater(array $session, string $body, string $saveName): string
{
    $form = parseSurveyForm($body);
    if ($form === null) {
        throw new RuntimeException('「稍后继续」找不到问卷表单');
    }
    $savePage = httpRequest(
        $form['action'],
        array_merge($form['fields'], ['saveall' => 'saveall']),
        $session['jar']
    );
    $saveForm = parseSurveyForm($savePage['body']);
    if ($saveForm === null || !isset($saveForm['fields']['savesubmit'])) {
        throw new RuntimeException('「稍后继续」没有返回保存表单');
    }
    $saved = httpRequest($saveForm['action'], array_merge($saveForm['fields'], [
        'savename' => $saveName,
        'savepass' => $saveName,
        'savepass2' => $saveName,
        'saveemail' => '',
    ]), $session['jar']);
    return $saved['body'];
}

/**
 * 续答入口：loadall=reload ＋ 名字口令（application/controllers/survey/SurveyIndex.php:456）。
 *
 * @param array{jar: string} $session
 */
function loadSavedResponse(array $session, int $surveyId, string $saveName): string
{
    $query = http_build_query([
        'lang' => 'en',
        'loadall' => 'reload',
        'loadname' => $saveName,
        'loadpass' => $saveName,
    ]);
    return httpRequest(BASE_URL . "/$surveyId?$query", null, $session['jar'])['body'];
}

/**
 * 走引擎真正的上传端点（multipart），拿到临时文件名。
 *
 * @param array{jar: string} $session
 * @return array<string, mixed>
 */
function uploadFile(array $session, string $fieldName, string $csrfToken): array
{
    $path = sys_get_temp_dir() . '/' . UPLOAD_FILE_NAME;
    file_put_contents($path, UPLOAD_FILE_BODY);
    // 上传端点也走 CSRF 校验，令牌只能从当前问卷页的表单里取。
    $curl = curl_init(BASE_URL . '/uploader/index?mode=upload&fieldname=' . $fieldName);
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_COOKIEJAR => $session['jar'],
        CURLOPT_COOKIEFILE => $session['jar'],
        CURLOPT_POSTFIELDS => [
            'YII_CSRF_TOKEN' => $csrfToken,
            'mode' => 'upload',
            'fieldname' => $fieldName,
            'uploadfile' => new CURLFile($path, 'text/plain', UPLOAD_FILE_NAME),
        ],
        CURLOPT_TIMEOUT => 60,
    ]);
    $body = (string) curl_exec($curl);
    curl_close($curl);
    @unlink($path);

    $decoded = json_decode($body, true);
    if (!is_array($decoded) || empty($decoded['success'])) {
        throw new RuntimeException('上传失败：' . substr($body, 0, 300));
    }
    return [
        'title' => '',
        'comment' => '',
        'size' => $decoded['size'],
        'name' => $decoded['name'],
        'filename' => $decoded['filename'],
        'ext' => $decoded['ext'],
    ];
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

/**
 * @param array<string, string>|null $postFields
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

// ------------------------------------------------------------- 发布与主题

function publish(string $key): int
{
    $lss = base64_encode((string) file_get_contents(SURVEY_FIXTURE));
    $surveyId = (int) assertRpcOk(rpc('import_survey', [$key, $lss, 'lss']), 'import_survey');
    assertRpcOk(rpc('activate_survey', [$key, $surveyId]), 'activate_survey');
    return $surveyId;
}

function installQuestionTheme(): void
{
    $theme = QuestionTheme::model()->findByAttributes(['name' => THEME_NAME]);
    if ($theme === null) {
        $theme = new QuestionTheme();
    }
    $theme->importManifest(THEME_DIR, true, true);
}

function uninstallQuestionTheme(): void
{
    $theme = QuestionTheme::model()->findByAttributes(['name' => THEME_NAME]);
    if ($theme !== null) {
        $theme->delete();
    }
}

/**
 * 题目代码 → 字段名映射。重建它是每次发布后的必做动作（ADR 0005 决定 3）。
 *
 * @return array<string, array<string, mixed>>
 */
function questionMap(PDO $db, int $surveyId): array
{
    $statement = $db->prepare(
        'SELECT qid, parent_qid, title FROM ' . table('questions') . ' WHERE sid = ? ORDER BY qid'
    );
    $statement->execute([$surveyId]);
    $map = [];
    $children = [];
    foreach ($statement->fetchAll(PDO::FETCH_ASSOC) as $row) {
        if ((int) $row['parent_qid'] === 0) {
            $map[$row['title']] = ['qid' => (int) $row['qid'], 'field' => 'Q' . (int) $row['qid'], 'subquestions' => []];
            continue;
        }
        $children[(int) $row['parent_qid']][$row['title']] = (int) $row['qid'];
    }
    foreach ($map as $code => $entry) {
        $map[$code]['subquestions'] = $children[$entry['qid']] ?? [];
    }
    return $map;
}

// --------------------------------------------------------------- 合并导出

/**
 * 主表 ＋ 副表的合并导出：平台真正要的形状。
 *
 * @return array<string, mixed>
 */
function combinedExport(PDO $db, int $surveyId, int $responseId): array
{
    return [
        'responseId' => $responseId,
        'QTABLE' => sideTableRows($db, $surveyId, $responseId, 'QTABLE'),
        'uploads' => uploadSessions($db, $surveyId, $responseId, 'QUPLOAD'),
    ];
}

// ----------------------------------------------------------------- 数据库

/**
 * @return array<int, array<string, string>>
 */
function sideTableRows(PDO $db, int $surveyId, int $responseId, string $questionCode): array
{
    $statement = $db->prepare(
        'SELECT row_index, column_code, cell_value FROM ' . table('mjyquestionextensions_answer_cell')
        . ' WHERE survey_id = ? AND generation = ? AND response_id = ? AND question_code = ?'
        . ' ORDER BY row_index, id'
    );
    $statement->execute([$surveyId, generation($db, $surveyId), $responseId, $questionCode]);
    $rows = [];
    foreach ($statement->fetchAll(PDO::FETCH_ASSOC) as $cell) {
        $rows[(int) $cell['row_index']][(string) $cell['column_code']] = (string) $cell['cell_value'];
    }
    ksort($rows);
    return array_values($rows);
}

/**
 * @return array<string, mixed>
 */
function sideTableState(PDO $db, int $surveyId, int $responseId, string $questionCode): array
{
    $statement = $db->prepare(
        'SELECT * FROM ' . table('mjyquestionextensions_answer_state')
        . ' WHERE survey_id = ? AND generation = ? AND response_id = ? AND question_code = ?'
    );
    $statement->execute([$surveyId, generation($db, $surveyId), $responseId, $questionCode]);
    $row = $statement->fetch(PDO::FETCH_ASSOC);
    return $row === false ? ['is_valid' => -1, 'errors' => ''] : $row;
}

/**
 * @return array<int, array<string, mixed>>
 */
function uploadSessions(PDO $db, int $surveyId, int $responseId, string $questionCode): array
{
    $statement = $db->prepare(
        'SELECT * FROM ' . table('mjyquestionextensions_upload_session')
        . ' WHERE survey_id = ? AND generation = ? AND response_id = ? AND question_code = ? ORDER BY id'
    );
    $statement->execute([$surveyId, generation($db, $surveyId), $responseId, $questionCode]);
    return $statement->fetchAll(PDO::FETCH_ASSOC);
}

function generation(PDO $db, int $surveyId): string
{
    $statement = $db->prepare('SELECT generation FROM ' . table('mjyplatformbridge_generation') . ' WHERE survey_id = ?');
    $statement->execute([$surveyId]);
    $generation = $statement->fetchColumn();
    if ($generation === false) {
        throw new RuntimeException("问卷 $surveyId 还没有代次");
    }
    return (string) $generation;
}

/**
 * @return array<string, mixed>
 */
function questionRow(PDO $db, int $surveyId, string $code): array
{
    $statement = $db->prepare(
        'SELECT * FROM ' . table('questions') . ' WHERE sid = ? AND title = ? AND parent_qid = 0'
    );
    $statement->execute([$surveyId, $code]);
    $row = $statement->fetch(PDO::FETCH_ASSOC);
    if ($row === false) {
        throw new RuntimeException("问卷 $surveyId 里找不到题目 $code");
    }
    return $row;
}

function questionAttribute(PDO $db, int $qid, string $attribute): ?string
{
    $statement = $db->prepare(
        'SELECT value FROM ' . table('question_attributes') . ' WHERE qid = ? AND attribute = ?'
    );
    $statement->execute([$qid, $attribute]);
    $value = $statement->fetchColumn();
    return $value === false ? null : (string) $value;
}

/**
 * @return string[]
 */
function responseColumns(PDO $db, int $surveyId): array
{
    return array_keys(responseRow($db, $surveyId, 0, true));
}

function responseColumnType(PDO $db, int $surveyId, string $column): string
{
    $table = table("responses_$surveyId");
    $statement = $db->prepare(
        'SELECT data_type FROM information_schema.columns WHERE table_name = ? AND column_name = ?'
    );
    $statement->execute([$table, $column]);
    return strtolower((string) $statement->fetchColumn());
}

/**
 * @return array<string, mixed>
 */
function responseRow(PDO $db, int $surveyId, int $responseId, bool $schemaOnly = false): array
{
    $table = table("responses_$surveyId");
    if ($schemaOnly) {
        $row = $db->query("SELECT * FROM $table WHERE 1 = 0");
        $columns = [];
        for ($index = 0; $index < $row->columnCount(); $index++) {
            $columns[$row->getColumnMeta($index)['name']] = null;
        }
        return $columns;
    }
    $statement = $db->prepare("SELECT * FROM $table WHERE id = ?");
    $statement->execute([$responseId]);
    $row = $statement->fetch(PDO::FETCH_ASSOC);
    if ($row === false) {
        throw new RuntimeException("答卷 $responseId 不存在");
    }
    return $row;
}

/**
 * 断点记录（lime_saved_control）：identifier 就是作答者填的名字。
 *
 * @return array<string, mixed>|null
 */
function savedControlRow(PDO $db, int $surveyId, string $saveName): ?array
{
    $statement = $db->prepare(
        'SELECT * FROM ' . table('saved_control') . ' WHERE sid = ? AND identifier = ?'
    );
    $statement->execute([$surveyId, $saveName]);
    $row = $statement->fetch(PDO::FETCH_ASSOC);
    return $row === false ? null : $row;
}

function latestResponseId(PDO $db, int $surveyId): int
{
    return (int) $db->query('SELECT MAX(id) FROM ' . table("responses_$surveyId"))->fetchColumn();
}

// ------------------------------------------------------------ RemoteControl

function login(): string
{
    $key = rpc('get_session_key', [ADMIN_USER, ADMIN_PASSWORD]);
    if (!is_string($key)) {
        throw new RuntimeException('RemoteControl 登录失败：' . json_encode($key));
    }
    return $key;
}

/**
 * @param mixed $response
 * @return mixed
 */
function assertRpcOk($response, string $method)
{
    if (is_array($response) && isset($response['status']) && !in_array($response['status'], ['OK', 'success'], true)) {
        throw new RuntimeException("$method 失败：" . json_encode($response, JSON_UNESCAPED_UNICODE));
    }
    return $response;
}

/**
 * @param array<int, mixed> $params
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
        CURLOPT_TIMEOUT => RPC_TIMEOUT_SECONDS,
    ]);
    $body = curl_exec($curl);
    curl_close($curl);
    $decoded = json_decode((string) $body, true);
    if (!is_array($decoded) || !array_key_exists('result', $decoded)) {
        throw new RuntimeException("RemoteControl $method 返回：" . substr((string) $body, 0, 300));
    }
    return $decoded['result'];
}

function exportLss(int $surveyId): SimpleXMLElement
{
    Survey::model()->resetCache();
    $parsed = simplexml_load_string(surveyGetXMLData($surveyId));
    if ($parsed === false) {
        throw new RuntimeException("LSS 导出解析失败：问卷 $surveyId");
    }
    return $parsed;
}

// ------------------------------------------------------------------- 环境

function bootstrapEngine(): void
{
    define('BASEPATH', '.');
    define('EXT', '.php');
    defined('STDIN') or define('STDIN', fopen('php://stdin', 'r'));
    require_once 'vendor/autoload.php';
    require_once 'vendor/yiisoft/yii/framework/yii.php';

    $settings = require 'application/config/config-defaults.php';
    $config = require 'application/config/internal.php';
    $config['components']['session']['class'] = 'ConsoleHttpSession';
    $config['components']['session']['cookieMode'] = 'none';
    $config['components']['session']['cookieParams'] = [];
    if (isset($config['config'])) {
        $settings = array_merge($settings, $config['config']);
    }
    unset($config['defaultController'], $config['config']);
    $config['runtimePath'] = $settings['tempdir'] . '/runtime';

    require_once 'application/core/ConsoleApplication.php';
    Yii::createApplication('ConsoleApplication', $config);
    Yii::app()->loadHelper('admin/import');
    Yii::app()->loadHelper('export');
    Yii::app()->loadHelper('common');
}

function connect(): PDO
{
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
    $db->prepare(
        "INSERT INTO $plugins (name, plugin_type, active, priority, version, load_error)"
        . " VALUES (?, 'user', 1, ?, '0.1.0', 0)"
    )->execute([$name, $priority]);
}

/**
 * @param array<string, bool> $checks
 * @return array<string, mixed>
 */
function result(string $scenario, array $checks): array
{
    $passed = !in_array(false, $checks, true);
    info(($passed ? 'PASS ' : 'FAIL ') . $scenario);
    return ['scenario' => $scenario, 'passed' => $passed, 'checks' => $checks];
}

function info(string $message): void
{
    fwrite(STDERR, "[e2e] $message\n");
}
