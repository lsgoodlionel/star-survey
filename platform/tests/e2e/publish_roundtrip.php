<?php

/**
 * P0-00.6 发布能力端到端验证：LSS 覆盖面、映射稳定性、激活后漂移面。
 *
 * 在 survey-test-web 内运行（见 run-publish-roundtrip.sh）。四个场景：
 *   1. LSS 覆盖面：导入 fixture → 激活 → 建参与者表 → 导出 LSS，
 *      断言哪些小节被携带、哪些（令牌/权限/全局题型主题/问卷组设置/激活状态）不被携带。
 *   2. 映射稳定性：同一份 .lss 导入两次，比较两份 get_fieldmap，
 *      判定平台的题目 UUID 映射能否以“题目代码”为键。
 *   3. 漂移面：问卷激活后仍被引擎接受的结构变更，以及归一化指纹能否检出。
 *   4. 激活失败与清理：一致性检查失败不留半发布状态；delete_survey 清理干净。
 *
 * 全部场景通过时退出码为 0。
 */

declare(strict_types=1);

const BASE_URL = 'http://localhost/index.php';
const ADMIN_USER = 'admin';
const ADMIN_PASSWORD = 'password';
const SURVEY_FIXTURE = 'tests/data/surveys/survey-dual-scale-question-api-test.lss';
const QUESTION_FIXTURE = 'tests/data/surveys/limesurvey_question_import_question_test.lsq';
const RPC_TIMEOUT_SECONDS = 180;

/** LSS 顶层小节：问卷结构导出必须携带的部分。 */
const CARRIED_SECTIONS = [
    'languages',
    'answers',
    'answer_l10ns',
    'groups',
    'group_l10ns',
    'questions',
    'subquestions',
    'question_l10ns',
    'question_attributes',
    'surveys',
    'surveys_languagesettings',
    'surveys_groups',
    'themes',
];

/** LSS 顶层小节：即使库里有数据也绝不会出现的部分（平台必须另行处理）。 */
const ABSENT_SECTIONS = [
    'tokens',
    'participants',
    'participant_attribute',
    'permissions',
    'permissiontemplates',
    'labelsets',
    'labels',
    'label_l10ns',
    'question_themes',
    'templates',
    'template_configuration',
    'surveys_groupsettings',
    'saved_control',
    'survey_links',
    'settings_global',
    'users',
];

/** 导出时从 surveys 行里排除的列（application/helpers/export_helper.php:1002）。 */
const SURVEY_COLUMNS_NOT_EXPORTED = ['active', 'owner_id', 'datecreated'];

main();

function main(): void
{
    bootstrapEngine();
    $db = connect();
    enableRemoteControl($db);
    $key = login();
    try {
        $results = [
            scenarioLssCoverage($db, $key),
            scenarioMappingStability($key),
            scenarioActiveDrift($db, $key),
            scenarioActivationCleanup($db, $key),
        ];
    } finally {
        rpc('release_session_key', [$key]);
    }

    $failed = array_filter($results, static function (array $result): bool {
        return !$result['passed'];
    });
    $summary = ['driver' => Yii::app()->db->getDriverName(), 'scenarios' => $results];
    echo json_encode($summary, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), "\n";
    exit(count($failed) === 0 ? 0 : 1);
}

// ------------------------------------------------------------------- 场景一

/**
 * LSS 导出携带什么、不携带什么。先把“不携带”的东西真的造出来（激活、
 * 参与者表、参与者、问卷权限），再导出，这样缺席才是证据而不是巧合。
 */
function scenarioLssCoverage(PDO $db, string $key): array
{
    $surveyId = importFixture($key, SURVEY_FIXTURE);
    assertRpcOk(rpc('activate_survey', [$key, $surveyId]), 'activate_survey');
    assertRpcOk(rpc('activate_tokens', [$key, $surveyId]), 'activate_tokens');
    $participant = [['email' => 'p0@example.invalid', 'lastname' => 'Publish', 'firstname' => 'Roundtrip']];
    assertRpcOk(rpc('add_participants', [$key, $surveyId, $participant, true]), 'add_participants');

    $xml = exportLss($surveyId);
    $sections = array_keys(sectionRowCounts($xml));
    $missing = array_values(array_diff(CARRIED_SECTIONS, array_merge($sections, ['languages', 'themes'])));
    $leaked = array_values(array_intersect(ABSENT_SECTIONS, $sections));
    $surveyRowColumns = array_keys((array) $xml->surveys->rows->row[0]);

    $reimportedId = (int) assertRpcOk(rpc('import_survey', [$key, base64_encode($xml->asXML()), 'lss']), 'import_survey');
    $checks = [
        '必带小节齐全' => $missing === [],
        '不带小节确实缺席' => $leaked === [],
        '参与者表有数据但未导出' => countRows($db, "tokens_$surveyId") === 1 && !in_array('tokens', $sections, true),
        '问卷权限有数据但未导出' => surveyPermissionCount($db, $surveyId) > 0 && !in_array('permissions', $sections, true),
        '全局题型主题未导出但题目保留主题名' => countRows($db, 'question_themes') > 0
            && !in_array('question_themes', $sections, true)
            && in_array('question_theme_name', array_keys((array) $xml->questions->rows->row[0]), true),
        '问卷组设置未导出' => countRows($db, 'surveys_groupsettings') > 0
            && !in_array('surveys_groupsettings', $sections, true),
        '激活态与属主不导出' => array_intersect(SURVEY_COLUMNS_NOT_EXPORTED, $surveyRowColumns) === [],
        '重新导入得到未激活问卷' => surveyActiveFlag($db, $reimportedId) === 'N'
            && !hasTable($db, "responses_$reimportedId"),
    ];

    $result = result('LSS 覆盖面', $checks);
    $result['evidence'] = [
        'surveyId' => $surveyId,
        'sections' => $sections,
        'xmlRowCounts' => sectionRowCounts($xml),
        'databaseRowCounts' => structuralRowCounts($db, $surveyId),
        'missingCarriedSections' => $missing,
        'leakedAbsentSections' => $leaked,
    ];
    return $result;
}

// ------------------------------------------------------------------- 场景二

/**
 * 同一份 .lss 导入两次：数字主键与字段名全变，题目代码/子题代码/尺度不变。
 */
function scenarioMappingStability(string $key): array
{
    $firstId = importFixture($key, SURVEY_FIXTURE);
    $secondId = importFixture($key, SURVEY_FIXTURE);
    $firstMap = fetchFieldmap($key, $firstId);
    $secondMap = fetchFieldmap($key, $secondId);

    $firstNames = array_keys($firstMap);
    $secondNames = array_keys($secondMap);
    $firstQids = questionIds($firstMap);
    $secondQids = questionIds($secondMap);
    $firstSignature = fieldmapSignature($firstMap);
    $secondSignature = fieldmapSignature($secondMap);

    $checks = [
        '问卷 id 不同' => $firstId !== $secondId,
        '字段名逐条不同' => array_values(array_diff($firstNames, $secondNames)) === array_values(array_diff($firstNames, metaFieldNames())),
        'qid 集合完全不相交' => array_intersect($firstQids, $secondQids) === [],
        '归一化签名逐条相等' => $firstSignature === $secondSignature,
        '签名指纹相等' => fingerprint($firstSignature) === fingerprint($secondSignature),
    ];

    $result = result('映射稳定性', $checks);
    $result['evidence'] = [
        'firstSurveyId' => $firstId,
        'secondSurveyId' => $secondId,
        'firstFieldNames' => $firstNames,
        'secondFieldNames' => $secondNames,
        'signatureSample' => array_slice($firstSignature, 5, 6),
        'fingerprint' => fingerprint($firstSignature),
    ];
    return $result;
}

// ------------------------------------------------------------------- 场景三

/**
 * 激活后引擎仍接受哪些结构变更，指纹能否把结构漂移和纯文本改动区分开。
 */
function scenarioActiveDrift(PDO $db, string $key): array
{
    $surveyId = importFixture($key, SURVEY_FIXTURE);
    assertRpcOk(rpc('activate_survey', [$key, $surveyId]), 'activate_survey');
    $questions = rpc('list_questions', [$key, $surveyId]);
    $parent = firstQuestionByType($questions, 'L');
    $child = firstSubquestion($questions);
    $columnsBefore = responseColumns($db, $surveyId);

    $baseline = fingerprint(fieldmapSignature(fetchFieldmap($key, $surveyId)));
    $attempts = driftAttempts($key, $surveyId, (int) $parent['qid'], (int) $child['qid']);
    $afterText = $attempts['改题干文本']['fingerprint'];
    $afterCode = $attempts['改题目代码']['fingerprint'];
    $afterSubCode = $attempts['改子题代码']['fingerprint'];

    $checks = [
        '改题干文本被接受' => $attempts['改题干文本']['accepted'],
        '改题干文本不触发指纹变化' => $afterText === $baseline,
        '改必答被接受且不触发指纹变化' => $attempts['改必答']['accepted']
            && $attempts['改必答']['fingerprint'] === $baseline,
        '改题目代码被接受' => $attempts['改题目代码']['accepted'],
        '改题目代码触发指纹变化' => $afterCode !== $afterText,
        '改子题代码被接受' => $attempts['改子题代码']['accepted'],
        '改子题代码触发指纹变化' => $afterSubCode !== $afterCode,
        '改题型被拒绝' => !$attempts['改题型']['accepted'],
        '激活后导入题目被拒绝' => !$attempts['导入题目']['accepted'],
        '激活后新增题组被拒绝' => !$attempts['新增题组']['accepted'],
        '激活后删除题目被拒绝' => !$attempts['删除题目']['accepted'],
        '改问卷文本属性被接受' => $attempts['改问卷联系人']['accepted'],
        '改匿名化被拒绝' => !$attempts['改匿名化']['accepted'],
        '改布局与题目索引被接受' => $attempts['改布局']['accepted'],
        '代码漂移不改动答卷表列' => responseColumns($db, $surveyId) === $columnsBefore,
    ];

    $result = result('激活后漂移面', $checks);
    $result['evidence'] = [
        'surveyId' => $surveyId,
        'baselineFingerprint' => $baseline,
        'attempts' => $attempts,
        'responseColumns' => $columnsBefore,
    ];
    return $result;
}

/**
 * 依次尝试各种激活后变更，每次记录引擎应答与变更后的指纹。
 *
 * @return array<string, array{accepted: bool, response: mixed, fingerprint: ?string}>
 */
function driftAttempts(string $key, int $surveyId, int $parentQid, int $childQid): array
{
    $groupId = (int) rpc('list_groups', [$key, $surveyId])[0]['gid'];
    $question = base64_encode((string) file_get_contents(QUESTION_FIXTURE));
    $plan = [
        '改题干文本' => ['set_question_properties', [$key, $parentQid, ['question' => 'P0 仅改文本']]],
        '改必答' => ['set_question_properties', [$key, $parentQid, ['mandatory' => 'Y']]],
        '改题目代码' => ['set_question_properties', [$key, $parentQid, ['title' => 'DRIFTQ']]],
        '改子题代码' => ['set_question_properties', [$key, $childQid, ['title' => 'DRIFTSQ']]],
        '改题型' => ['set_question_properties', [$key, $parentQid, ['type' => 'T']]],
        '导入题目' => ['import_question', [$key, $surveyId, $groupId, $question, 'lsq']],
        '新增题组' => ['add_group', [$key, $surveyId, 'P0 漂移题组', '']],
        '删除题目' => ['delete_question', [$key, $parentQid]],
        '改问卷联系人' => ['set_survey_properties', [$key, $surveyId, ['admin' => 'P0 漂移联系人']]],
        '改匿名化' => ['set_survey_properties', [$key, $surveyId, ['anonymized' => 'Y']]],
        '改布局' => ['set_survey_properties', [$key, $surveyId, ['format' => 'A', 'questionindex' => 1]]],
    ];

    $attempts = [];
    foreach ($plan as $label => [$method, $params]) {
        $response = rpc($method, $params);
        $attempts[$label] = [
            'accepted' => isRpcAccepted($response),
            'response' => $response,
            'fingerprint' => fingerprint(fieldmapSignature(fetchFieldmap($key, $surveyId))),
        ];
        info("$label => " . json_encode($response, JSON_UNESCAPED_UNICODE));
    }
    return $attempts;
}

// ------------------------------------------------------------------- 场景四

/**
 * 激活失败不留半发布状态；delete_survey 把结构、答卷表、参与者表、权限一并清理。
 */
function scenarioActivationCleanup(PDO $db, string $key): array
{
    $withoutGroup = (int) assertRpcOk(rpc('add_survey', [$key, 0, 'P0 无题组 ' . gmdate('c'), 'en', 'A']), 'add_survey');
    $noGroupResponse = rpc('activate_survey', [$key, $withoutGroup]);

    $withEmptyGroup = (int) assertRpcOk(rpc('add_survey', [$key, 0, 'P0 空题组 ' . gmdate('c'), 'en', 'A']), 'add_survey');
    assertRpcOk(rpc('add_group', [$key, $withEmptyGroup, 'P0 空题组', '']), 'add_group');
    $emptyGroupResponse = rpc('activate_survey', [$key, $withEmptyGroup]);

    $withoutAnswers = importFixture($key, SURVEY_FIXTURE);
    stripAnswerOptions($db, $withoutAnswers, 'L');
    $noAnswerResponse = rpc('activate_survey', [$key, $withoutAnswers]);

    // 先取证再删除：删除后 active 标记和结构行都查不到，断言会失去意义。
    $isHalfPublished = hasTable($db, "responses_$withoutGroup")
        || hasTable($db, "responses_$withEmptyGroup")
        || surveyActiveFlag($db, $withoutGroup) !== 'N'
        || surveyActiveFlag($db, $withEmptyGroup) !== 'N';

    $activeId = importFixture($key, SURVEY_FIXTURE);
    assertRpcOk(rpc('activate_survey', [$key, $activeId]), 'activate_survey');
    assertRpcOk(rpc('activate_tokens', [$key, $activeId]), 'activate_tokens');
    assertRpcOk(rpc('delete_survey', [$key, $activeId]), 'delete_survey');
    assertRpcOk(rpc('delete_survey', [$key, $withEmptyGroup]), 'delete_survey');

    $checks = [
        '无题组激活被一致性检查拒绝' => !isRpcAccepted($noGroupResponse),
        '空题组激活被一致性检查拒绝' => !isRpcAccepted($emptyGroupResponse),
        '激活失败不留半发布状态' => !$isHalfPublished,
        '缺答案选项仍被 RPC 激活（引擎缺口）' => isRpcAccepted($noAnswerResponse),
        '删除激活问卷清理答卷表与参与者表' => !hasTable($db, "responses_$activeId")
            && !hasTable($db, "tokens_$activeId"),
        '删除问卷清理结构与权限' => structuralRowCounts($db, $withEmptyGroup) === emptyStructuralCounts()
            && surveyActiveFlag($db, $withEmptyGroup) === ''
            && surveyPermissionCount($db, $withEmptyGroup) === 0,
    ];

    $result = result('激活失败与清理', $checks);
    $result['evidence'] = [
        'withoutGroup' => ['surveyId' => $withoutGroup, 'response' => $noGroupResponse],
        'withEmptyGroup' => ['surveyId' => $withEmptyGroup, 'response' => $emptyGroupResponse],
        'withoutAnswerOptions' => ['surveyId' => $withoutAnswers, 'response' => $noAnswerResponse],
        'deletedActiveSurveyId' => $activeId,
    ];
    return $result;
}

// ------------------------------------------------------------------- 指纹

/**
 * 把 get_fieldmap 归一化成“代码＋题型＋尺度”的有序列表，丢掉一切数字主键。
 *
 * @param array<string, array<string, mixed>> $fieldmap
 * @return string[]
 */
function fieldmapSignature(array $fieldmap): array
{
    $signature = [];
    foreach ($fieldmap as $row) {
        $signature[] = implode('|', [
            (string) $row['type'],
            (string) ($row['title'] ?? ''),
            (string) ($row['aid'] ?? ''),
            (string) ($row['scale_id'] ?? ''),
        ]);
    }
    return $signature;
}

/**
 * @param string[] $signature
 */
function fingerprint(array $signature): string
{
    return substr(hash('sha256', implode("\n", $signature)), 0, 16);
}

/**
 * @param array<string, array<string, mixed>> $fieldmap
 * @return int[]
 */
function questionIds(array $fieldmap): array
{
    $ids = [];
    foreach ($fieldmap as $row) {
        if (!empty($row['qid'])) {
            $ids[] = (int) $row['qid'];
        }
    }
    return array_values(array_unique($ids));
}

/**
 * 答卷表固有列，不随题目变化。
 *
 * @return string[]
 */
function metaFieldNames(): array
{
    return ['id', 'submitdate', 'lastpage', 'startlanguage', 'seed', 'token', 'startdate', 'datestamp'];
}

/**
 * @return array<string, array<string, mixed>>
 */
function fetchFieldmap(string $key, int $surveyId): array
{
    $fieldmap = rpc('get_fieldmap', [$key, $surveyId]);
    if (!is_array($fieldmap) || isset($fieldmap['status'])) {
        throw new RuntimeException('get_fieldmap 失败：' . json_encode($fieldmap, JSON_UNESCAPED_UNICODE));
    }
    return $fieldmap;
}

// --------------------------------------------------------------- LSS 导出

function exportLss(int $surveyId): SimpleXMLElement
{
    Survey::model()->resetCache();
    $parsed = simplexml_load_string(surveyGetXMLData($surveyId));
    if ($parsed === false) {
        throw new RuntimeException("LSS 导出解析失败：问卷 $surveyId");
    }
    return $parsed;
}

/**
 * 顶层小节 → 行数。只统计带 <rows><row> 的小节。
 *
 * @return array<string, int>
 */
function sectionRowCounts(SimpleXMLElement $xml): array
{
    $counts = [];
    foreach ($xml as $tag => $section) {
        if (isset($section->rows->row)) {
            $counts[(string) $tag] = count($section->rows->row);
        } elseif (in_array((string) $tag, ['languages', 'themes', 'themes_inherited'], true)) {
            $counts[(string) $tag] = count($section->children());
        }
    }
    return $counts;
}

// ------------------------------------------------------------ RemoteControl

function login(): string
{
    $key = rpc('get_session_key', [ADMIN_USER, ADMIN_PASSWORD]);
    if (!is_string($key)) {
        throw new RuntimeException('RemoteControl 登录失败：' . json_encode($key, JSON_UNESCAPED_UNICODE));
    }
    return $key;
}

function importFixture(string $key, string $path): int
{
    $lss = base64_encode((string) file_get_contents($path));
    $surveyId = assertRpcOk(rpc('import_survey', [$key, $lss, 'lss']), 'import_survey');
    info("imported $path => survey $surveyId");
    return (int) $surveyId;
}

/**
 * 引擎在失败时返回带 status/error_code 的数组，成功时返回标量或逐字段布尔表。
 *
 * @param mixed $response
 */
function isRpcAccepted($response): bool
{
    if (!is_array($response)) {
        return true;
    }
    if (isset($response['error_code'])) {
        return false;
    }
    if (isset($response['status'])) {
        return in_array($response['status'], ['OK', 'success'], true);
    }
    return true;
}

/**
 * @param mixed $response
 * @return mixed
 */
function assertRpcOk($response, string $method)
{
    if (!isRpcAccepted($response)) {
        throw new RuntimeException("$method 失败：" . json_encode($response, JSON_UNESCAPED_UNICODE));
    }
    return $response;
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

/**
 * @param array<int, array<string, mixed>> $questions
 * @return array<string, mixed>
 */
function firstQuestionByType(array $questions, string $type): array
{
    foreach ($questions as $question) {
        if ($question['type'] === $type && (int) $question['parent_qid'] === 0) {
            return $question;
        }
    }
    throw new RuntimeException("fixture 中没有题型 $type 的题目");
}

/**
 * @param array<int, array<string, mixed>> $questions
 * @return array<string, mixed>
 */
function firstSubquestion(array $questions): array
{
    foreach ($questions as $question) {
        if ((int) $question['parent_qid'] > 0) {
            return $question;
        }
    }
    throw new RuntimeException('fixture 中没有子题');
}

// -------------------------------------------------------------------- 环境

/**
 * 以控制台方式启动引擎，仅为了在进程内调用 surveyGetXMLData()。
 * 与 application/commands/console.php 的启动步骤一致，但不执行命令。
 */
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
    define('APPPATH', Yii::app()->getBasePath() . DIRECTORY_SEPARATOR);
    Yii::app()->loadHelper('export');
}

function connect(): PDO
{
    $connection = Yii::app()->db;
    $pdo = new PDO($connection->connectionString, $connection->username, $connection->password);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $GLOBALS['tablePrefix'] = $connection->tablePrefix;
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

function hasTable(PDO $db, string $name): bool
{
    $statement = $db->prepare('SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?');
    $statement->execute([table($name)]);
    return (int) $statement->fetchColumn() > 0;
}

/**
 * @return string[]
 */
function responseColumns(PDO $db, int $surveyId): array
{
    $statement = $db->prepare(
        'SELECT column_name FROM information_schema.columns WHERE table_name = ? ORDER BY column_name'
    );
    $statement->execute([table("responses_$surveyId")]);
    return $statement->fetchAll(PDO::FETCH_COLUMN);
}

function countRows(PDO $db, string $name, string $where = '1=1'): int
{
    return (int) $db->query('SELECT COUNT(*) FROM ' . table($name) . " WHERE $where")->fetchColumn();
}

function surveyActiveFlag(PDO $db, int $surveyId): string
{
    $statement = $db->prepare('SELECT active FROM ' . table('surveys') . ' WHERE sid = ?');
    $statement->execute([$surveyId]);
    return (string) $statement->fetchColumn();
}

function surveyPermissionCount(PDO $db, int $surveyId): int
{
    return countRows($db, 'permissions', "entity = 'survey' AND entity_id = $surveyId");
}

/**
 * 结构表的行数，用来对照 LSS 小节以及验证删除是否干净。
 *
 * @return array<string, int>
 */
function structuralRowCounts(PDO $db, int $surveyId): array
{
    $questions = table('questions');
    return [
        'groups' => countRows($db, 'groups', "sid = $surveyId"),
        'group_l10ns' => countRows($db, 'group_l10ns', "gid IN (SELECT gid FROM " . table('groups') . " WHERE sid = $surveyId)"),
        'questions' => countRows($db, 'questions', "sid = $surveyId AND parent_qid = 0"),
        'subquestions' => countRows($db, 'questions', "sid = $surveyId AND parent_qid > 0"),
        'question_l10ns' => countRows($db, 'question_l10ns', "qid IN (SELECT qid FROM $questions WHERE sid = $surveyId)"),
        'question_attributes' => countRows($db, 'question_attributes', "qid IN (SELECT qid FROM $questions WHERE sid = $surveyId)"),
        'answers' => countRows($db, 'answers', "qid IN (SELECT qid FROM $questions WHERE sid = $surveyId)"),
        'surveys_languagesettings' => countRows($db, 'surveys_languagesettings', "surveyls_survey_id = $surveyId"),
    ];
}

/**
 * @return array<string, int>
 */
function emptyStructuralCounts(): array
{
    return array_fill_keys(
        ['groups', 'group_l10ns', 'questions', 'subquestions', 'question_l10ns', 'question_attributes', 'answers', 'surveys_languagesettings'],
        0
    );
}

/**
 * 删掉某题型的答案选项，模拟 DSL → LSS 生成器漏掉选项的情况。
 */
function stripAnswerOptions(PDO $db, int $surveyId, string $type): void
{
    $statement = $db->prepare('SELECT qid FROM ' . table('questions') . ' WHERE sid = ? AND parent_qid = 0 AND type = ?');
    $statement->execute([$surveyId, $type]);
    $questionIds = $statement->fetchAll(PDO::FETCH_COLUMN);
    if ($questionIds === []) {
        throw new RuntimeException("fixture 中没有题型 $type 的题目");
    }
    $list = implode(',', array_map('intval', $questionIds));
    $answers = table('answers');
    $db->exec("DELETE FROM " . table('answer_l10ns') . " WHERE aid IN (SELECT aid FROM $answers WHERE qid IN ($list))");
    $db->exec("DELETE FROM $answers WHERE qid IN ($list)");
}

/**
 * @param array<string, bool> $checks
 * @return array{scenario: string, passed: bool, checks: array<string, bool>}
 */
function result(string $scenario, array $checks): array
{
    $passed = !in_array(false, $checks, true);
    info(($passed ? 'PASS ' : 'FAIL ') . $scenario);
    foreach ($checks as $label => $ok) {
        if (!$ok) {
            info("  失败断言：$label");
        }
    }
    return ['scenario' => $scenario, 'passed' => $passed, 'checks' => $checks];
}

function info(string $message): void
{
    fwrite(STDERR, "[e2e] $message\n");
}
