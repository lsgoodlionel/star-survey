<?php

/**
 * P0-00.5 租户隔离越权穷举。
 *
 * 在租户 A 的引擎实例内部运行（见 platform/deploy/tenancy/run-isolation.sh），
 * 扮演“拿到租户 A 全部权限的人”，穷举访问租户 B 的数据库、RemoteControl、
 * 填写运行时、上传文件、后台答卷路由与共享运行时目录。
 *
 * 验证栈故意把两个租户放在同一个 docker 网络里：网络层是通的，所以每一次拒绝
 * 都只能来自数据库授权、引擎会话/权限、实例路由或每租户独立卷——这正是要证明的。
 *
 * 任何一次“应当被拒绝”的尝试没有被拒绝，都会进入 findings 并让脚本非零退出。
 * 常量与探测原语见同目录的 tenancy_probes.php。
 */

declare(strict_types=1);

require_once __DIR__ . '/tenancy_probes.php';

main();

function main(): void
{
    $ownDb = connectOwnDatabase();
    seedTenantFixtures(NEIGHBOUR_ROOT, NEIGHBOUR_ADMIN_USER, NEIGHBOUR_ADMIN_PASSWORD, [SHARED_SID, VICTIM_SID], CANARY);
    seedTenantFixtures(OWN_ROOT, OWN_ADMIN_USER, OWN_ADMIN_PASSWORD, [SHARED_SID], DECOY);
    info('fixtures ready');

    $scenarios = array_merge(
        databaseScenarios($ownDb),
        remoteControlScenarios(),
        runtimeScenarios(),
        fileScenarios(),
        adminScenarios(),
        sharedStateScenarios()
    );
    cleanupProbes();
    report($scenarios);
}

// ------------------------------------------------------------------ 场景：直连数据库

/**
 * @return array<int, array<string, mixed>>
 */
function databaseScenarios(PDO $ownDb): array
{
    return [
        scenario('db', 'A 的数据库账号连 B 的数据库服务器', [
            attemptFrom(
                'PDO ' . NEIGHBOUR_DB_DSN . ' user=tenant_a password=tenant-a-db-pass',
                connectAs(NEIGHBOUR_DB_DSN, 'tenant_a', 'tenant-a-db-pass'),
                EXPECT_DENIED,
                BY_DB_AUTH
            ),
            attemptFrom(
                'PDO ' . NEIGHBOUR_DB_DSN . ' user=tenant_a password=tenant-b-db-pass（猜口令）',
                connectAs(NEIGHBOUR_DB_DSN, 'tenant_a', NEIGHBOUR_DB_PASSWORD),
                EXPECT_DENIED,
                BY_DB_AUTH
            ),
            attemptFrom(
                '网络可达性对照：PDO ' . NEIGHBOUR_DB_DSN . ' 使用 B 自己的账号口令',
                connectAs(NEIGHBOUR_DB_DSN, NEIGHBOUR_DB_USER, NEIGHBOUR_DB_PASSWORD),
                EXPECT_OBSERVE,
                BY_NONE
            ),
        ]),
        scenario('db', 'A 的账号读写同服务器共置的 B 库', [
            attemptFrom('tenant_a@db-a: USE tenant_b', sqlAttempt($ownDb, 'USE tenant_b'), EXPECT_DENIED, BY_DB_GRANT),
            attemptFrom(
                'tenant_a@db-a: SELECT secret FROM tenant_b.canary',
                sqlAttempt($ownDb, 'SELECT secret FROM tenant_b.canary'),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                'tenant_a@db-a: INSERT INTO tenant_b.canary',
                sqlAttempt($ownDb, "INSERT INTO tenant_b.canary (id, secret) VALUES (2, 'written-by-a')"),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                'tenant_a@db-a: DROP DATABASE tenant_b',
                sqlAttempt($ownDb, 'DROP DATABASE tenant_b'),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
        ]),
        scenario('db', 'A 的账号枚举与提权', [
            attemptFrom(
                'tenant_a@db-a: SHOW DATABASES 是否出现 tenant_b',
                rowsAttempt($ownDb, 'SHOW DATABASES', NEIGHBOUR_DB_NAME),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                "tenant_a@db-a: SELECT table_name FROM information_schema.tables WHERE table_schema='tenant_b'",
                rowsAttempt(
                    $ownDb,
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='tenant_b'",
                    null
                ),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                'tenant_a@db-a: SELECT user, host FROM mysql.user',
                sqlAttempt($ownDb, 'SELECT user, host FROM mysql.user'),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                "tenant_a@db-a: GRANT ALL PRIVILEGES ON *.* TO 'tenant_a'@'%'",
                sqlAttempt($ownDb, "GRANT ALL PRIVILEGES ON *.* TO 'tenant_a'@'%'"),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                "tenant_a@db-a: SELECT LOAD_FILE('/etc/passwd')",
                loadFileAttempt($ownDb),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                'tenant_a@db-a: CREATE DATABASE tenancy_probe',
                sqlAttempt($ownDb, 'CREATE DATABASE tenancy_probe'),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
            attemptFrom(
                'tenant_a@db-a 自己的 lime_users 中是否出现 B 的管理员',
                rowsAttempt($ownDb, 'SELECT users_name FROM lime_users', NEIGHBOUR_ADMIN_USER),
                EXPECT_DENIED,
                BY_DB_GRANT
            ),
        ]),
    ];
}

// ------------------------------------------------------- 场景：RemoteControl / REST

/**
 * @return array<int, array<string, mixed>>
 */
function remoteControlScenarios(): array
{
    $ownKey = sessionKey(OWN_ROOT, OWN_ADMIN_USER, OWN_ADMIN_PASSWORD);
    $scenarios = [
        scenario('rpc', 'A 的管理员凭据打 B 的 RemoteControl', [
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol get_session_key(admin_a, tenant-a-secret)',
                rpcAttempt(NEIGHBOUR_ROOT, 'get_session_key', [OWN_ADMIN_USER, OWN_ADMIN_PASSWORD]),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol get_session_key(admin_b, tenant-a-secret)',
                rpcAttempt(NEIGHBOUR_ROOT, 'get_session_key', [NEIGHBOUR_ADMIN_USER, OWN_ADMIN_PASSWORD]),
                EXPECT_DENIED,
                BY_ENGINE
            ),
        ]),
        scenario('rpc', 'A 的 session key 打 B 的实例', [
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol list_surveys(A 的 key)',
                rpcAttempt(NEIGHBOUR_ROOT, 'list_surveys', [$ownKey]),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol export_responses(A 的 key, ' . VICTIM_SID . ', json)',
                rpcAttempt(NEIGHBOUR_ROOT, 'export_responses', [$ownKey, VICTIM_SID, 'json']),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol get_survey_properties(A 的 key, ' . SHARED_SID . ')',
                rpcAttempt(NEIGHBOUR_ROOT, 'get_survey_properties', [$ownKey, SHARED_SID]),
                EXPECT_DENIED,
                BY_ENGINE
            ),
        ]),
        scenario('rpc', '未认证与伪造 key 打 B 的实例', [
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol list_surveys("")',
                rpcAttempt(NEIGHBOUR_ROOT, 'list_surveys', ['']),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol export_responses(伪造 key, ' . VICTIM_SID . ')',
                rpcAttempt(NEIGHBOUR_ROOT, 'export_responses', [str_repeat('a', 32), VICTIM_SID, 'json']),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'POST ' . NEIGHBOUR_ROOT . '/index.php/admin/remotecontrol get_summary(null, ' . VICTIM_SID . ')',
                rpcAttempt(NEIGHBOUR_ROOT, 'get_summary', [null, VICTIM_SID]),
                EXPECT_DENIED,
                BY_ENGINE
            ),
        ]),
        scenario('rpc', 'A 在自己实例上使用 B 的问卷 id', [
            attemptFrom(
                'POST ' . OWN_ROOT . '/index.php/admin/remotecontrol export_responses(A 的 key, ' . VICTIM_SID . ')',
                rpcAttempt(OWN_ROOT, 'export_responses', [$ownKey, VICTIM_SID, 'json']),
                EXPECT_DENIED,
                BY_ROUTING
            ),
            attemptFrom(
                'POST ' . OWN_ROOT . '/index.php/admin/remotecontrol get_summary(A 的 key, ' . VICTIM_SID . ')',
                rpcAttempt(OWN_ROOT, 'get_summary', [$ownKey, VICTIM_SID]),
                EXPECT_DENIED,
                BY_ROUTING
            ),
            attemptFrom(
                '同号问卷：POST ' . OWN_ROOT . '/index.php/admin/remotecontrol export_responses(A 的 key, ' . SHARED_SID . ')',
                rpcAttempt(OWN_ROOT, 'export_responses', [$ownKey, SHARED_SID, 'json']),
                EXPECT_DENIED,
                BY_ROUTING
            ),
        ]),
    ];
    rpcCall(OWN_ROOT, 'release_session_key', [$ownKey]);
    return $scenarios;
}

// ------------------------------------------------------------------ 场景：填写运行时

/**
 * @return array<int, array<string, mixed>>
 */
function runtimeScenarios(): array
{
    $enumeration = [];
    foreach (range(VICTIM_SID - 2, VICTIM_SID + 2) as $sid) {
        $response = request(OWN_ROOT . '/index.php/' . $sid);
        $enumeration[] = attemptFrom(
            'GET ' . OWN_ROOT . '/index.php/' . $sid,
            httpOutcome($response),
            EXPECT_DENIED,
            BY_ROUTING
        );
    }
    return [
        scenario('runtime', '在 A 上打开 B 的问卷 id', [
            attemptFrom(
                'GET ' . OWN_ROOT . '/index.php/' . VICTIM_SID . '?lang=en&newtest=Y',
                httpOutcome(request(OWN_ROOT . '/index.php/' . VICTIM_SID . '?lang=en&newtest=Y')),
                EXPECT_DENIED,
                BY_ROUTING
            ),
            attemptFrom(
                'GET ' . OWN_ROOT . '/index.php?sid=' . VICTIM_SID . '&newtest=Y',
                httpOutcome(request(OWN_ROOT . '/index.php?sid=' . VICTIM_SID . '&newtest=Y')),
                EXPECT_DENIED,
                BY_ROUTING
            ),
            attemptFrom(
                'GET ' . OWN_ROOT . '/index.php/' . VICTIM_SID . '?srid=1&newtest=Y（借用 B 的答卷 id）',
                httpOutcome(request(OWN_ROOT . '/index.php/' . VICTIM_SID . '?srid=1&newtest=Y')),
                EXPECT_DENIED,
                BY_ROUTING
            ),
            attemptFrom(
                '阳性对照：GET ' . NEIGHBOUR_ROOT . '/index.php/' . VICTIM_SID . ' 在 B 上确实有内容',
                httpOutcome(request(NEIGHBOUR_ROOT . '/index.php/' . VICTIM_SID . '?lang=en&newtest=Y')),
                EXPECT_ALLOWED,
                BY_NONE
            ),
        ]),
        scenario('runtime', '同号问卷的 id 命名空间', [
            attemptFrom(
                'GET ' . OWN_ROOT . '/index.php/' . SHARED_SID . ' 是否返回 B 的同号问卷内容',
                httpOutcome(request(OWN_ROOT . '/index.php/' . SHARED_SID . '?lang=en&newtest=Y')),
                EXPECT_DENIED,
                BY_ROUTING
            ),
        ]),
        scenario('runtime', '在 A 上穷举问卷 id', $enumeration),
    ];
}

// ------------------------------------------------------------------ 场景：上传文件

/**
 * @return array<int, array<string, mixed>>
 */
function fileScenarios(): array
{
    $victimPath = 'upload/surveys/' . VICTIM_SID . '/files/' . VICTIM_UPLOAD_FILE;
    return [
        scenario('files', '通过 A 取 B 的答卷上传文件', [
            attemptFrom(
                'A 容器内 file_get_contents(' . $victimPath . ')',
                localFileOutcome($victimPath),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
            attemptFrom(
                'GET ' . OWN_ROOT . '/' . $victimPath,
                httpOutcome(request(OWN_ROOT . '/' . $victimPath)),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
            attemptFrom(
                'GET ' . OWN_ROOT . '/upload/surveys/' . VICTIM_SID . '/files/%2e%2e%2f%2e%2e%2f（目录穿越）',
                httpOutcome(request(OWN_ROOT . '/upload/surveys/' . VICTIM_SID . '/files/%2e%2e%2f%2e%2e%2f')),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
        ]),
        scenario('files', '未认证直接取 B 实例上的上传文件', [
            attemptFrom(
                'GET ' . NEIGHBOUR_ROOT . '/' . $victimPath . '（无任何凭据）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/' . $victimPath)),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'GET ' . NEIGHBOUR_ROOT . '/upload/surveys/（目录列举）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/upload/surveys/')),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'GET ' . NEIGHBOUR_ROOT . '/upload/surveys/' . VICTIM_SID . '/files/（目录列举）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/upload/surveys/' . VICTIM_SID . '/files/')),
                EXPECT_DENIED,
                BY_ENGINE
            ),
            attemptFrom(
                'GET ' . NEIGHBOUR_ROOT . '/tmp/runtime/application.log（引擎日志）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/tmp/runtime/application.log')),
                EXPECT_DENIED,
                BY_ENGINE
            ),
        ]),
    ];
}

// ------------------------------------------------------------------ 场景：后台路由

/**
 * @return array<int, array<string, mixed>>
 */
function adminScenarios(): array
{
    $cookieJar = tempnam(sys_get_temp_dir(), 'tenancy-admin');
    try {
        $isLoggedIn = adminLogin(OWN_ROOT, OWN_ADMIN_USER, OWN_ADMIN_PASSWORD, $cookieJar);
        $stolenSession = ['headers' => ['Cookie: PHPSESSID=' . cookieValue($cookieJar, 'PHPSESSID')]];
        $browseVictim = '/index.php/responses/browse/surveyId/' . VICTIM_SID;
        $exportVictim = '/index.php/admin/export/sa/exportresults/surveyid/' . VICTIM_SID;
        $exportOwn = '/index.php/admin/export/sa/exportresults/surveyid/' . SHARED_SID;
        $downloadVictim = '/index.php/responses/downloadfile/surveyId/' . VICTIM_SID . '/responseId/1/qid/1/index/0';
        return [
            scenario('admin', 'A 的后台会话打 B 的实例', [
                attemptFrom(
                    '阳性对照：A 的管理员在 A 上登录成功',
                    ['denied' => !$isLoggedIn, 'observed' => $isLoggedIn ? '登录成功，拿到 PHPSESSID' : '登录失败'],
                    EXPECT_ALLOWED,
                    BY_NONE
                ),
                attemptFrom(
                    'GET ' . NEIGHBOUR_ROOT . '/index.php/admin （请求头带 A 的 PHPSESSID）',
                    httpOutcome(request(NEIGHBOUR_ROOT . '/index.php/admin', $stolenSession)),
                    EXPECT_DENIED,
                    BY_ENGINE
                ),
                attemptFrom(
                    'GET ' . NEIGHBOUR_ROOT . $browseVictim . ' （请求头带 A 的 PHPSESSID）',
                    httpOutcome(request(NEIGHBOUR_ROOT . $browseVictim, $stolenSession)),
                    EXPECT_DENIED,
                    BY_ENGINE
                ),
                attemptFrom(
                    'GET ' . NEIGHBOUR_ROOT . $exportVictim . ' （请求头带 A 的 PHPSESSID）',
                    httpOutcome(request(NEIGHBOUR_ROOT . $exportVictim, $stolenSession)),
                    EXPECT_DENIED,
                    BY_ENGINE
                ),
            ]),
            scenario('admin', 'A 的后台会话在 A 上使用 B 的 sid', [
                attemptFrom(
                    'GET ' . OWN_ROOT . $browseVictim,
                    httpOutcome(request(OWN_ROOT . $browseVictim, ['cookieJar' => $cookieJar])),
                    EXPECT_DENIED,
                    BY_ROUTING
                ),
                attemptFrom(
                    'GET ' . OWN_ROOT . $exportVictim,
                    httpOutcome(request(OWN_ROOT . $exportVictim, ['cookieJar' => $cookieJar])),
                    EXPECT_DENIED,
                    BY_ROUTING
                ),
                attemptFrom(
                    'GET ' . OWN_ROOT . $downloadVictim,
                    httpOutcome(request(OWN_ROOT . $downloadVictim, ['cookieJar' => $cookieJar])),
                    EXPECT_DENIED,
                    BY_ROUTING
                ),
                attemptFrom(
                    '同号问卷：GET ' . OWN_ROOT . '/index.php/responses/browse/surveyId/' . SHARED_SID,
                    httpOutcome(request(OWN_ROOT . '/index.php/responses/browse/surveyId/' . SHARED_SID, ['cookieJar' => $cookieJar])),
                    EXPECT_DENIED,
                    BY_ROUTING
                ),
                attemptFrom(
                    '阳性对照：GET ' . OWN_ROOT . $exportOwn . '（A 自己的问卷，导出路由可达）',
                    httpOutcome(request(OWN_ROOT . $exportOwn, ['cookieJar' => $cookieJar]), DECOY . '-TITLE'),
                    EXPECT_ALLOWED,
                    BY_NONE
                ),
            ]),
            scenario('admin', '未认证打 B 的后台答卷路由', [
                attemptFrom(
                    'GET ' . NEIGHBOUR_ROOT . $browseVictim . '（无 cookie）',
                    httpOutcome(request(NEIGHBOUR_ROOT . $browseVictim)),
                    EXPECT_DENIED,
                    BY_ENGINE
                ),
                attemptFrom(
                    'GET ' . NEIGHBOUR_ROOT . $exportVictim . '（无 cookie）',
                    httpOutcome(request(NEIGHBOUR_ROOT . $exportVictim)),
                    EXPECT_DENIED,
                    BY_ENGINE
                ),
            ]),
        ];
    } finally {
        @unlink($cookieJar);
    }
}

// --------------------------------------------------- 场景：共享缓存、临时目录与代码树

/**
 * @return array<int, array<string, mixed>>
 */
function sharedStateScenarios(): array
{
    writeProbe(OWN_TMP_PROBE, 'TENANT-A-TMP-PROBE');
    writeProbe(SHARED_TREE_PROBE, 'TENANT-A-SHARED-TREE-PROBE');
    writeProbe(SHARED_TREE_CODE_PROBE, codeProbeSource());
    return [
        scenario('shared-state', 'tmp 运行时目录是否互通', [
            attemptFrom(
                '阳性对照：GET ' . OWN_ROOT . '/' . OWN_TMP_PROBE . '（A 自己写的标记）',
                httpOutcome(request(OWN_ROOT . '/' . OWN_TMP_PROBE), 'TENANT-A-TMP-PROBE'),
                EXPECT_ALLOWED,
                BY_NONE
            ),
            attemptFrom(
                'GET ' . NEIGHBOUR_ROOT . '/' . OWN_TMP_PROBE . '（B 是否看得见 A 的 tmp）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/' . OWN_TMP_PROBE), 'TENANT-A-TMP-PROBE'),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
            attemptFrom(
                '两个实例的会话 cookie 名称是否相同',
                cookieNameOutcome(),
                EXPECT_OBSERVE,
                BY_NONE
            ),
            attemptFrom(
                'A 容器内 session.save_path',
                ['denied' => true, 'observed' => 'session.save_path=' . (ini_get('session.save_path') ?: '(PHP 默认 /tmp，容器内私有)')],
                EXPECT_OBSERVE,
                BY_FILESYSTEM
            ),
        ]),
        scenario('shared-state', '共享代码树是否成为跨实例通道', [
            attemptFrom(
                'A 写入 ' . SHARED_TREE_PROBE . ' 后 GET ' . NEIGHBOUR_ROOT . '/' . SHARED_TREE_PROBE,
                httpOutcome(request(NEIGHBOUR_ROOT . '/' . SHARED_TREE_PROBE), 'TENANT-A-SHARED-TREE-PROBE'),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
            attemptFrom(
                'GET ' . NEIGHBOUR_ROOT . '/platform/phpunit.xml（共享代码树里的既有文件）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/platform/phpunit.xml'), 'phpunit'),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
            attemptFrom(
                'A 写入 ' . SHARED_TREE_CODE_PROBE . ' 后 GET ' . NEIGHBOUR_ROOT . '/' . SHARED_TREE_CODE_PROBE . '（由 B 的实例执行）',
                httpOutcome(request(NEIGHBOUR_ROOT . '/' . SHARED_TREE_CODE_PROBE)),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
            attemptFrom(
                'A 容器内检查 application/config/security.php 是否位于共享代码树',
                sharedSecretsOutcome(),
                EXPECT_DENIED,
                BY_FILESYSTEM
            ),
        ]),
    ];
}

/**
 * 两个实例都用默认的 PHPSESSID：同一个域名下只靠端口区分时，浏览器会把
 * 同名 cookie 发给两边，互相覆盖。
 *
 * @return array{denied: bool, observed: string}
 */
function cookieNameOutcome(): array
{
    $own = tempnam(sys_get_temp_dir(), 'tenancy-cookie-a');
    $neighbour = tempnam(sys_get_temp_dir(), 'tenancy-cookie-b');
    try {
        request(OWN_ROOT . '/index.php/admin/authentication/sa/login', ['cookieJar' => $own]);
        request(NEIGHBOUR_ROOT . '/index.php/admin/authentication/sa/login', ['cookieJar' => $neighbour]);
        $ownId = cookieValue($own, 'PHPSESSID');
        $neighbourId = cookieValue($neighbour, 'PHPSESSID');
        return [
            'denied' => $ownId === $neighbourId,
            'observed' => '两个实例都用 PHPSESSID，会话 id 不同（A=' . substr($ownId, 0, 8)
                . '…，B=' . substr($neighbourId, 0, 8) . '…）；同域不同端口时浏览器会互相覆盖',
        ];
    } finally {
        @unlink($own);
        @unlink($neighbour);
    }
}

/**
 * 探针源码：由邻居实例执行，用它自己的配置连自己的库，把问卷标题回显出来。
 * 共享代码树一旦成立，租户隔离就被整体击穿——这段代码就是证据。
 */
function codeProbeSource(): string
{
    return "<?php\n"
        . "// P0-00.5 临时探针，运行结束即删除。\n"
        . "define('BASEPATH', __DIR__);\n"
        . "\$config = require '/var/www/html/application/config/config.php';\n"
        . "\$db = \$config['components']['db'];\n"
        . "\$pdo = new PDO(\$db['connectionString'], \$db['username'], \$db['password']);\n"
        . "\$sql = 'SELECT surveyls_title FROM lime_surveys_languagesettings WHERE surveyls_survey_id = " . VICTIM_SID . "';\n"
        . "echo (string) \$pdo->query(\$sql)->fetchColumn();\n";
}

/**
 * 探针只为取证，跑完立刻删掉，避免在仓库里留下可执行文件。
 */
function cleanupProbes(): void
{
    foreach ([SHARED_TREE_PROBE, SHARED_TREE_CODE_PROBE, OWN_TMP_PROBE] as $path) {
        if (is_file($path)) {
            unlink($path);
        }
    }
    if (is_dir(dirname(SHARED_TREE_PROBE))) {
        @rmdir(dirname(SHARED_TREE_PROBE));
    }
}

/**
 * 两个实例共用同一份 application/config/security.php 时，加密密钥也是共用的。
 *
 * @return array{denied: bool, observed: string}
 */
function sharedSecretsOutcome(): array
{
    $path = 'application/config/security.php';
    if (!is_file($path)) {
        return ['denied' => true, 'observed' => "$path 不存在，两个实例各自生成密钥"];
    }
    $isSharedMount = !is_file('/proc/mounts')
        || strpos((string) file_get_contents('/proc/mounts'), ' /var/www/html ') === false;
    $fingerprint = substr(hash_file('sha256', $path), 0, 16);
    return [
        'denied' => false,
        'observed' => "$path 存在于共享代码树（sha256 前缀 {$fingerprint}），两个实例加载同一份加密密钥"
            . ($isSharedMount ? '' : '；/var/www/html 为绑定挂载'),
    ];
}
