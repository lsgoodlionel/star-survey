<?php

/**
 * P0-00.5 租户隔离越权穷举的公共探测工具。
 *
 * 这里只放与具体场景无关的东西：验证栈参数、单次尝试与场景的结果结构、
 * 数据库 / HTTP / RemoteControl 探测原语，以及金丝雀夹具的建立。
 * 场景本身在 platform/tests/e2e/tenancy_isolation.php。
 */

declare(strict_types=1);

// 下列常量必须与 platform/deploy/tenancy/lib.sh 保持一致。
const OWN_ROOT = 'http://localhost';
const NEIGHBOUR_ROOT = 'http://web-b';
const OWN_ADMIN_USER = 'admin_a';
const OWN_ADMIN_PASSWORD = 'tenant-a-secret';
const NEIGHBOUR_ADMIN_USER = 'admin_b';
const NEIGHBOUR_ADMIN_PASSWORD = 'tenant-b-secret';
const NEIGHBOUR_DB_DSN = 'mysql:host=db-b;port=3306;dbname=tenant_b;';
const NEIGHBOUR_DB_USER = 'tenant_b';
const NEIGHBOUR_DB_PASSWORD = 'tenant-b-db-pass';
const NEIGHBOUR_DB_NAME = 'tenant_b';
const SHARED_SID = 555001;          // 两个实例上都存在的同号问卷
const VICTIM_SID = 555002;          // 只有租户 B 有
const VICTIM_UPLOAD_FILE = 'fu_tenantbcanary0';
const CANARY = 'TENANT-B-CANARY';   // 出现在响应里即视为泄漏
const DECOY = 'TENANT-A-DECOY';
const QUESTION_FIXTURE = 'tests/data/surveys/limesurvey_question_import_question_test.lsq';
const SHARED_TREE_PROBE = 'platform/deploy/tenancy/probe/shared-tree-probe.txt';
const SHARED_TREE_CODE_PROBE = 'platform/deploy/tenancy/probe/shared-tree-probe.php';
const OWN_TMP_PROBE = 'tmp/tenancy-probe-a.txt';
const HTTP_TIMEOUT = 60;

// 拒绝来自哪一层。
const BY_DB_AUTH = 'db-auth';         // 对方 DB 服务器上没有这个账号
const BY_DB_GRANT = 'db-grant';       // DB 授权拒绝
const BY_ROUTING = 'routing';         // 实例路由：对方的 id 在本实例不存在
const BY_ENGINE = 'engine';           // 引擎会话校验或权限检查
const BY_FILESYSTEM = 'filesystem';   // 每租户独立卷
const BY_NONE = 'none';               // 无任何机制阻止

// 期望：denied=必须被拒绝；allowed=阳性对照，必须成功；observation=只记录不判定。
const EXPECT_DENIED = 'denied';
const EXPECT_ALLOWED = 'allowed';
const EXPECT_OBSERVE = 'observation';
// ------------------------------------------------------------------ 夹具

/**
 * 在某个实例上重建金丝雀问卷：固定 sid、固定标题与答案标记。
 *
 * @param int[] $surveyIds
 */
function seedTenantFixtures(string $root, string $user, string $password, array $surveyIds, string $marker): void
{
    $key = sessionKey($root, $user, $password);
    try {
        foreach ($surveyIds as $surveyId) {
            if (surveyExists($root, $key, $surveyId)) {
                continue; // 重建会连带删掉该问卷的上传目录，夹具做成幂等的
            }
            $created = rpcCall($root, 'add_survey', [$key, $surveyId, "$marker-TITLE-$surveyId", 'en', 'A']);
            if ((int) $created !== $surveyId) {
                throw new RuntimeException("add_survey($surveyId) 返回 " . json_encode($created));
            }
            $groupId = rpcCall($root, 'add_group', [$key, $surveyId, 'G1', '']);
            $lsq = base64_encode((string) file_get_contents(QUESTION_FIXTURE));
            $questionId = rpcCall($root, 'import_question', [$key, $surveyId, $groupId, $lsq, 'lsq', 'N']);
            rpcCall($root, 'set_survey_properties', [$key, $surveyId, ['usecaptcha' => 'N']]);
            rpcCall($root, 'activate_survey', [$key, $surveyId]);
            $field = $surveyId . 'X' . $groupId . 'X' . $questionId;
            rpcCall($root, 'add_response', [$key, $surveyId, [$field => "$marker-ANSWER"]]);
        }
    } finally {
        rpcCall($root, 'release_session_key', [$key]);
    }
}

function surveyExists(string $root, string $key, int $surveyId): bool
{
    $properties = rpcCall($root, 'get_survey_properties', [$key, $surveyId]);
    return is_array($properties) && array_key_exists('sid', $properties);
}

function sessionKey(string $root, string $user, string $password): string
{
    $key = rpcCall($root, 'get_session_key', [$user, $password]);
    if (!is_string($key)) {
        throw new RuntimeException("$root get_session_key 失败：" . json_encode($key));
    }
    return $key;
}

// ------------------------------------------------------------------ 尝试与结果

/**
 * @param array{denied: bool, observed: string} $outcome
 * @return array<string, mixed>
 */
function attemptFrom(string $request, array $outcome, string $expectation, string $enforcedBy): array
{
    $isDenied = $outcome['denied'];
    $passed = match ($expectation) {
        EXPECT_DENIED => $isDenied,
        EXPECT_ALLOWED => !$isDenied,
        default => true,
    };
    return [
        'request' => $request,
        'observed' => $outcome['observed'],
        'expectation' => $expectation,
        'denied' => $isDenied,
        'enforcedBy' => $isDenied && $expectation === EXPECT_DENIED ? $enforcedBy : BY_NONE,
        'passed' => $passed,
    ];
}

/**
 * @param array<int, array<string, mixed>> $attempts
 * @return array<string, mixed>
 */
function scenario(string $group, string $name, array $attempts): array
{
    $failed = array_filter($attempts, static fn(array $attempt): bool => !$attempt['passed']);
    $passed = count($failed) === 0;
    info(($passed ? 'PASS ' : 'FAIL ') . "[$group] $name");
    return ['group' => $group, 'scenario' => $name, 'passed' => $passed, 'attempts' => $attempts];
}

/**
 * @param array<int, array<string, mixed>> $scenarios
 */
function report(array $scenarios): void
{
    $findings = [];
    foreach ($scenarios as $entry) {
        foreach ($entry['attempts'] as $attempt) {
            if (!$attempt['passed']) {
                $findings[] = [
                    'group' => $entry['group'],
                    'scenario' => $entry['scenario'],
                    'request' => $attempt['request'],
                    'observed' => $attempt['observed'],
                    'expectation' => $attempt['expectation'],
                ];
            }
        }
    }
    $summary = [
        'generatedAt' => gmdate('c'),
        'sharedSurveyId' => SHARED_SID,
        'victimSurveyId' => VICTIM_SID,
        'scenarioCount' => count($scenarios),
        'findingCount' => count($findings),
        'findings' => $findings,
        'scenarios' => $scenarios,
    ];
    echo json_encode($summary, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), "\n";
    exit(count($findings) === 0 ? 0 : 1);
}

// ------------------------------------------------------------------ 数据库工具

function connectOwnDatabase(): PDO
{
    if (!defined('BASEPATH')) {
        define('BASEPATH', getcwd() . '/'); // config.php 拒绝直接访问
    }
    $config = require 'application/config/config.php';
    $dbConfig = $config['components']['db'];
    $pdo = new PDO($dbConfig['connectionString'], $dbConfig['username'], $dbConfig['password']);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    return $pdo;
}

/**
 * @return array{denied: bool, observed: string}
 */
function connectAs(string $dsn, string $user, string $password): array
{
    try {
        $pdo = new PDO($dsn, $user, $password, [PDO::ATTR_TIMEOUT => 10]);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $database = (string) $pdo->query('SELECT DATABASE()')->fetchColumn();
        return ['denied' => false, 'observed' => "连接成功，当前库 $database"];
    } catch (PDOException $exception) {
        return ['denied' => true, 'observed' => trim($exception->getMessage())];
    }
}

/**
 * 执行一条 SQL：抛错视为被拒绝。
 *
 * @return array{denied: bool, observed: string}
 */
function sqlAttempt(PDO $db, string $sql): array
{
    try {
        $statement = $db->query($sql);
        $rows = $statement === false ? [] : $statement->fetchAll(PDO::FETCH_NUM);
        return ['denied' => false, 'observed' => '执行成功，返回 ' . count($rows) . ' 行：' . truncate(json_encode($rows))];
    } catch (PDOException $exception) {
        return ['denied' => true, 'observed' => trim($exception->getMessage())];
    }
}

/**
 * 执行一条查询：结果里出现 $forbidden 才算没被拒绝（$forbidden 为 null 时空结果即为拒绝）。
 *
 * @return array{denied: bool, observed: string}
 */
function rowsAttempt(PDO $db, string $sql, ?string $forbidden): array
{
    try {
        $rows = $db->query($sql)->fetchAll(PDO::FETCH_COLUMN);
    } catch (PDOException $exception) {
        return ['denied' => true, 'observed' => trim($exception->getMessage())];
    }
    $serialized = json_encode($rows, JSON_UNESCAPED_UNICODE);
    $isLeaked = $forbidden === null ? $rows !== [] : in_array($forbidden, $rows, true);
    return [
        'denied' => !$isLeaked,
        'observed' => '返回 ' . count($rows) . ' 行：' . truncate((string) $serialized),
    ];
}

/**
 * @return array{denied: bool, observed: string}
 */
function loadFileAttempt(PDO $db): array
{
    try {
        $content = $db->query("SELECT LOAD_FILE('/etc/passwd')")->fetchColumn();
    } catch (PDOException $exception) {
        return ['denied' => true, 'observed' => trim($exception->getMessage())];
    }
    if ($content === null || $content === false || $content === '') {
        return ['denied' => true, 'observed' => 'LOAD_FILE 返回 NULL（无 FILE 权限）'];
    }
    return ['denied' => false, 'observed' => '读到文件内容：' . truncate((string) $content)];
}

// ------------------------------------------------------------------ HTTP 工具

/**
 * @param array{cookieJar?: string, post?: array<string, string>, headers?: string[]} $options
 * @return array{status: int, body: string, error: ?string}
 */
function request(string $url, array $options = []): array
{
    $curl = curl_init($url);
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_TIMEOUT => HTTP_TIMEOUT,
    ]);
    if (isset($options['headers'])) {
        curl_setopt($curl, CURLOPT_HTTPHEADER, $options['headers']);
    }
    if (isset($options['cookieJar'])) {
        curl_setopt($curl, CURLOPT_COOKIEJAR, $options['cookieJar']);
        curl_setopt($curl, CURLOPT_COOKIEFILE, $options['cookieJar']);
    }
    if (isset($options['post'])) {
        curl_setopt($curl, CURLOPT_POST, true);
        curl_setopt($curl, CURLOPT_POSTFIELDS, http_build_query($options['post']));
    }
    $body = curl_exec($curl);
    $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $error = $body === false ? curl_error($curl) : null;
    curl_close($curl);
    return ['status' => $status, 'body' => $body === false ? '' : (string) $body, 'error' => $error];
}

/**
 * 响应体里出现禁止标记即视为“没有被拒绝”。
 *
 * @param array{status: int, body: string, error: ?string} $response
 * @return array{denied: bool, observed: string}
 */
function httpOutcome(array $response, string $forbidden = CANARY): array
{
    if ($response['error'] !== null) {
        return ['denied' => true, 'observed' => 'curl 失败：' . $response['error']];
    }
    $isLeaked = strpos($response['body'], $forbidden) !== false;
    $observed = 'HTTP ' . $response['status'] . '，' . strlen($response['body']) . ' 字节，'
        . ($isLeaked ? "响应中出现 $forbidden" : "响应中没有 $forbidden")
        . '；' . describeBody($response['body']);
    return ['denied' => !$isLeaked, 'observed' => $observed];
}

/**
 * 从页面里挑一点能说明情况的痕迹：标题、登录表单、错误提示。
 */
function describeBody(string $body): string
{
    if (preg_match('/id="loginform"/', $body) === 1) {
        return '页面是后台登录表单';
    }
    if (preg_match('/<title>\s*([^<]{0,80})/', $body, $matches) === 1) {
        return 'title=' . trim($matches[1]);
    }
    return '正文=' . truncate($body, 120);
}

/**
 * @return array{denied: bool, observed: string}
 */
function localFileOutcome(string $path): array
{
    if (!is_file($path)) {
        return ['denied' => true, 'observed' => "本实例文件系统中不存在 $path"];
    }
    return ['denied' => false, 'observed' => "读到内容：" . truncate((string) file_get_contents($path), 120)];
}

function writeProbe(string $path, string $content): void
{
    $directory = dirname($path);
    if (!is_dir($directory) && !mkdir($directory, 0777, true) && !is_dir($directory)) {
        throw new RuntimeException("无法创建目录 $directory");
    }
    if (file_put_contents($path, $content) === false) {
        throw new RuntimeException("无法写入 $path");
    }
}

/**
 * 走一遍真实的后台登录表单（含 CSRF 令牌）。
 */
function adminLogin(string $root, string $user, string $password, string $cookieJar): bool
{
    $loginUrl = $root . '/index.php/admin/authentication/sa/login';
    $page = request($loginUrl, ['cookieJar' => $cookieJar]);
    $fields = hiddenFormFields($page['body'], 'loginform');
    $fields['user'] = $user;
    $fields['password'] = $password;
    $fields['login_submit'] = 'login';
    $fields['loginlang'] = 'default';
    $result = request($loginUrl, ['cookieJar' => $cookieJar, 'post' => $fields]);
    return strpos($result['body'], 'id="loginform"') === false;
}

/**
 * 从 Netscape 格式的 cookie 文件里取出某个 cookie 的值。
 * curl 不会把 localhost 的 cookie 发给 web-b，跨实例攻击必须手工构造 Cookie 头。
 */
function cookieValue(string $cookieJar, string $name): string
{
    foreach (explode("\n", (string) file_get_contents($cookieJar)) as $line) {
        $columns = explode("\t", trim($line));
        if (count($columns) === 7 && $columns[5] === $name) {
            return $columns[6];
        }
    }
    throw new RuntimeException("cookie 文件里没有 $name");
}

/**
 * @return array<string, string>
 */
function hiddenFormFields(string $html, string $formId): array
{
    $document = new DOMDocument();
    libxml_use_internal_errors(true);
    $document->loadHTML($html);
    libxml_clear_errors();
    $form = $document->getElementById($formId);
    if ($form === null) {
        throw new RuntimeException("页面里没有 #$formId 表单");
    }
    $fields = [];
    foreach ($form->getElementsByTagName('input') as $input) {
        if ($input->getAttribute('type') === 'hidden' && $input->getAttribute('name') !== '') {
            $fields[$input->getAttribute('name')] = $input->getAttribute('value');
        }
    }
    return $fields;
}

// ------------------------------------------------------------ RemoteControl 工具

/**
 * 一次 JSON-RPC 调用，返回 result（出错时返回原始报文摘要）。
 *
 * @param array<int, mixed> $params
 * @return mixed
 */
function rpcCall(string $root, string $method, array $params)
{
    $curl = curl_init($root . '/index.php/admin/remotecontrol');
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
        CURLOPT_POSTFIELDS => json_encode(['method' => $method, 'params' => $params, 'id' => 1]),
        CURLOPT_TIMEOUT => HTTP_TIMEOUT,
    ]);
    $body = curl_exec($curl);
    curl_close($curl);
    $decoded = json_decode((string) $body, true);
    if (!is_array($decoded) || !array_key_exists('result', $decoded)) {
        throw new RuntimeException("RemoteControl $method 返回：" . truncate((string) $body, 300));
    }
    return $decoded['result'];
}

/**
 * RPC 越权尝试：返回值里出现金丝雀标记才算没被拒绝。
 *
 * @param array<int, mixed> $params
 * @return array{denied: bool, observed: string}
 */
function rpcAttempt(string $root, string $method, array $params): array
{
    try {
        $result = rpcCall($root, $method, $params);
    } catch (RuntimeException $exception) {
        return ['denied' => true, 'observed' => $exception->getMessage()];
    }
    $serialized = json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    $plain = is_string($result) ? base64_decode($result, true) : false;
    $isLeaked = strpos((string) $serialized, CANARY) !== false
        || ($plain !== false && strpos($plain, CANARY) !== false);
    return [
        'denied' => !$isLeaked,
        'observed' => 'result=' . truncate((string) $serialized, 200)
            . ($plain !== false ? '；base64 解码后=' . truncate($plain, 200) : ''),
    ];
}

// ------------------------------------------------------------------ 杂项

function truncate(string $value, int $limit = 160): string
{
    $flat = trim(preg_replace('/\s+/', ' ', $value) ?? $value);
    return strlen($flat) <= $limit ? $flat : substr($flat, 0, $limit) . '…';
}

function info(string $message): void
{
    fwrite(STDERR, "[tenancy] $message\n");
}
