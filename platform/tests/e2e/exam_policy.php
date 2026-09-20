<?php

/**
 * P0-00.7 端到端策略验证：真实 HTTP 作答、服务端计时、最后名额并发争抢。
 *
 * 在 survey-exam-web 容器里运行（见 platform/deploy/exam/run-exam-policy.sh）。
 * 通过 RemoteControl 建问卷，再像真实作答者一样用 curl 填写；并发部分用
 * curl_multi 让几十上百个请求同时到达，用来观察"最后一个名额"的真实归属。
 *
 * 四组场景：
 *   A 引擎基线   —— 引擎自己有什么：问卷级 expires、客户端时间戳是否被采信；
 *   B 服务端计时 —— 迟到请求／旧会话重放／换浏览器重开／伪造表单字段；
 *   C 硬配额     —— 引擎自带配额 vs 平台名额租约，各自在并发下放行了几个人；
 *   D 拒绝语义   —— beforeSurveyPage 到底能不能把一次提交挡在落库之前。
 *
 * 退出码 0 表示全部通过。
 */

declare(strict_types=1);

require_once __DIR__ . '/exam_policy_support.php';

const POLICY_PLUGIN = 'MjyRuntimePolicy';
const PLUGIN_PRIORITY = 0;
const DENY_DEADLINE_MARKER = '考试时间已结束';
const DENY_QUOTA_MARKER = '名额已满';
const EXPIRED_MARKER = 'survey is expired';
const EXAM_DURATION_SECONDS = 5;
const LEASE_TTL_SECONDS = 300;
const SHORT_LEASE_TTL_SECONDS = 3;
const LAST_SLOT_LIMIT = 3; // 先顺序占掉 2 个，再让并发去抢第 3 个（最后一个）
const ENGINE_QUOTA_ROUNDS_PER_LEVEL = 2;
const LOADED_QUOTA_ROUNDS = 3;
const LEASE_RACE_ROUNDS = 3;
const LOADED_QUOTA_CONCURRENCY = 8; // 低并发时请求最齐，竞态窗口最有代表性
const BULK_RESPONSE_ROWS = 200000;
const PADDING_ANSWER_CODE = 'AO02';
const ENGINE_QUOTA_MESSAGE = 'quota reached';
const FORGED_TIMESTAMP = '2099-01-01 00:00:00';

main();

function main(): void
{
    $db = connect();
    enableRemoteControl($db);
    enablePlugin($db, POLICY_PLUGIN, PLUGIN_PRIORITY);
    // 插件是直接改数据库启用的，没有走 beforeActivate：跑一次 cron 让它建表。
    runCron();

    $concurrency = (int) (getenv('EXAM_CONCURRENCY') ?: 100);
    $results = [];

    $examSurvey = createSurvey('P0 考试计时', true);
    $results[] = runScenario('引擎 expires 在服务端拦截', fn() => scenarioEngineExpiryIsServerSide($db, $examSurvey));
    $results[] = runScenario('伪造的时间戳字段不被采信', fn() => scenarioEngineIgnoresClientTimestamps($db, $examSurvey));

    applyPolicy($db, $examSurvey['sid'], EXAM_DURATION_SECONDS, 0, LEASE_TTL_SECONDS);
    $results[] = runScenario('超时提交被拒绝且不落库', fn() => scenarioLateRequestIsRejected($db, $examSurvey));
    $results[] = runScenario('旧会话重放被拒绝', fn() => scenarioReplayedSessionIsRejected($db, $examSurvey));
    $results[] = runScenario('换浏览器重开不能重新计时', fn() => scenarioReopenWithFreshCookiesIsRejected($db, $examSurvey));
    $results[] = runScenario('伪造的截止时刻字段无效', fn() => scenarioForgedFieldsDoNotExtendTheDeadline($db, $examSurvey));

    $engineQuotaSurvey = createSurvey('P0 引擎配额并发', false);
    defineEngineQuota($db, $engineQuotaSurvey, LAST_SLOT_LIMIT);
    $results[] = runScenario(
        '引擎自带配额的最后一个名额（并发档位扫描）',
        fn() => scenarioEngineQuotaLastSlot($db, $engineQuotaSurvey, $concurrency)
    );

    $loadedQuotaSurvey = createSurvey('P0 引擎配额高答卷量', false);
    defineEngineQuota($db, $loadedQuotaSurvey, LAST_SLOT_LIMIT);
    $results[] = runScenario(
        '高答卷量下引擎配额的最后一个名额',
        fn() => scenarioEngineQuotaUnderLoad($db, $loadedQuotaSurvey, LOADED_QUOTA_CONCURRENCY)
    );

    // 引擎配额与名额租约同时开着：租约在进场时定人数，引擎配额只是第二道保险。
    $leaseSurvey = createSurvey('P0 名额租约并发', false);
    defineEngineQuota($db, $leaseSurvey, LAST_SLOT_LIMIT);
    applyPolicy($db, $leaseSurvey['sid'], 0, 1, LEASE_TTL_SECONDS);
    $results[] = runScenario(
        '平台名额租约的最后一个名额',
        fn() => scenarioLeaseLastSlot($db, $leaseSurvey, $concurrency)
    );

    $expirySurvey = createSurvey('P0 租约过期归还', false);
    applyPolicy($db, $expirySurvey['sid'], 0, 1, SHORT_LEASE_TTL_SECONDS);
    $results[] = runScenario('过期租约归还名额', fn() => scenarioAbandonedLeaseIsReturned($db, $expirySurvey));

    $failed = array_filter($results, static fn(array $result): bool => !$result['passed']);
    echo json_encode(['scenarios' => $results], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE), "\n";
    exit(count($failed) === 0 ? 0 : 1);
}

// ------------------------------------------------------------ A 引擎基线

/**
 * 引擎自带的问卷到期是服务端判定：过期时间与 gmdate() 比较
 * （SurveyIndex.php:387），客户端怎么改时钟都没用。但它是**问卷级**的，
 * 所有人共享同一个到期时刻，不是"每人一场考试"。
 */
function scenarioEngineExpiryIsServerSide(PDO $db, array $survey): array
{
    $before = countResponses($db, $survey['sid']);
    setSurveyExpiry($db, $survey['sid'], gmdate('Y-m-d H:i:s', time() - 3600));
    $token = addToken($survey['sid']);
    $outcome = runRespondent($survey, $token);
    setSurveyExpiry($db, $survey['sid'], null);

    return result('引擎 expires 在服务端拦截', [
        '作答被拒绝' => !$outcome['completed'],
        '命中引擎到期页' => str_contains($outcome['body'], EXPIRED_MARKER),
        '没有写入任何答卷' => countResponses($db, $survey['sid']) === $before,
    ], ['expiredAt' => gmdate('Y-m-d H:i:s', time() - 3600)]);
}

/**
 * 表单里塞进 startdate / submitdate / datestamp / interviewtime，引擎照样写自己
 * 的服务端时间：时间戳不是客户端能提供的输入。
 */
function scenarioEngineIgnoresClientTimestamps(PDO $db, array $survey): array
{
    $token = addToken($survey['sid']);
    $forged = [
        'startdate' => FORGED_TIMESTAMP,
        'submitdate' => FORGED_TIMESTAMP,
        'datestamp' => FORGED_TIMESTAMP,
        'interviewtime' => '0',
    ];
    $outcome = runRespondent($survey, $token, $forged);
    $row = latestResponse($db, $survey['sid']);

    return result('伪造的时间戳字段不被采信', [
        '作答完成' => $outcome['completed'],
        'startdate 是服务端时间' => isRecentUtc((string) ($row['startdate'] ?? '')),
        'submitdate 是服务端时间' => isRecentUtc((string) ($row['submitdate'] ?? '')),
        '没有出现伪造值' => !in_array(FORGED_TIMESTAMP, [$row['startdate'] ?? '', $row['submitdate'] ?? ''], true),
    ], ['startdate' => $row['startdate'] ?? null, 'submitdate' => $row['submitdate'] ?? null]);
}

// ---------------------------------------------------------- B 服务端计时

/**
 * 进场之后超时才提交：请求到得再晚，判定的"现在"也来自数据库。
 * 这同时回答了"钩子能不能拒绝一次提交"：提交请求被挡在落库之前。
 */
function scenarioLateRequestIsRejected(PDO $db, array $survey): array
{
    $token = addToken($survey['sid']);
    $before = countResponses($db, $survey['sid']);
    $session = enterSurvey($survey, $token);
    // 引擎在打开问卷时就建好了答卷行（未提交），所以基线要取进场之后。
    $afterEntry = countResponses($db, $survey['sid']);
    sleep(EXAM_DURATION_SECONDS + 2);
    $submitted = submitPreparedPage($survey, $session);
    cleanupSession($session);
    $row = responseForToken($db, $survey, $token);

    return result('超时提交被拒绝且不落库', [
        '进场时拿到题目' => $session['entered'],
        '提交被拒绝' => !$submitted['completed'],
        '命中超时拒绝页' => str_contains($submitted['body'], DENY_DEADLINE_MARKER),
        '被拒绝的提交没有新增答卷' => countResponses($db, $survey['sid']) === $afterEntry,
        '答卷仍未提交' => $row !== null && $row['submitdate'] === null,
        '被拒绝的答案没有落库' => $row !== null && $row[$survey['quotaField']] === null,
        '截止时刻已入库' => deadlineOf($db, $survey['sid'], 'token:' . $token) !== null,
    ], ['进场时引擎新建的答卷行数' => $afterEntry - $before]);
}

/**
 * 旧会话重放：保留超时前的 Cookie，断线重连之后接着用。
 */
function scenarioReplayedSessionIsRejected(PDO $db, array $survey): array
{
    $token = addToken($survey['sid']);
    $session = enterSurvey($survey, $token);
    $afterEntry = countResponses($db, $survey['sid']);
    sleep(EXAM_DURATION_SECONDS + 2);
    // 同一份 Cookie 再次进入作答页，等价于断线重连／刷新。
    $replay = httpRequest(surveyUrl($survey['sid'], $token), null, $session['jar']);
    $submitted = submitPreparedPage($survey, $session);
    cleanupSession($session);
    $row = responseForToken($db, $survey, $token);

    return result('旧会话重放被拒绝', [
        '重连页面被拒绝' => str_contains($replay['body'], DENY_DEADLINE_MARKER),
        '提交被拒绝' => !$submitted['completed'],
        '没有新增答卷' => countResponses($db, $survey['sid']) === $afterEntry,
        '答卷仍未提交' => $row !== null && $row['submitdate'] === null,
    ]);
}

/**
 * 换浏览器重开：全新 Cookie、全新 PHP 会话，但准考证 token 不变。
 * 截止时刻按 token 存，所以重开不会重新开始计时。
 */
function scenarioReopenWithFreshCookiesIsRejected(PDO $db, array $survey): array
{
    $token = addToken($survey['sid']);
    $session = enterSurvey($survey, $token);
    $afterEntry = countResponses($db, $survey['sid']);
    $pinnedDeadline = deadlineOf($db, $survey['sid'], 'token:' . $token);
    cleanupSession($session);

    sleep(EXAM_DURATION_SECONDS + 2);
    $fresh = runRespondent($survey, $token);
    $deadlineAfter = deadlineOf($db, $survey['sid'], 'token:' . $token);
    $row = responseForToken($db, $survey, $token);

    return result('换浏览器重开不能重新计时', [
        '重开被拒绝' => !$fresh['completed'],
        '命中超时拒绝页' => str_contains($fresh['body'], DENY_DEADLINE_MARKER),
        '截止时刻没有被延长' => $pinnedDeadline !== null && $pinnedDeadline === $deadlineAfter,
        '没有新增答卷' => countResponses($db, $survey['sid']) === $afterEntry,
        '答卷仍未提交' => $row !== null && $row['submitdate'] === null,
    ], ['deadlineAt' => $pinnedDeadline]);
}

/**
 * 伪造表单字段：把插件自己的字段名、以及引擎的时间字段一起塞进 POST。
 */
function scenarioForgedFieldsDoNotExtendTheDeadline(PDO $db, array $survey): array
{
    $token = addToken($survey['sid']);
    $session = enterSurvey($survey, $token);
    $afterEntry = countResponses($db, $survey['sid']);
    sleep(EXAM_DURATION_SECONDS + 2);
    $forged = [
        'MjyDeadline' => FORGED_TIMESTAMP,
        'MjyRuntimePolicyDeadline' => FORGED_TIMESTAMP,
        'deadline_at' => FORGED_TIMESTAMP,
        'startdate' => FORGED_TIMESTAMP,
        'interviewtime' => '0',
    ];
    $submitted = submitPreparedPage($survey, $session, $forged);
    cleanupSession($session);
    $row = responseForToken($db, $survey, $token);

    return result('伪造的截止时刻字段无效', [
        '提交被拒绝' => !$submitted['completed'],
        '命中超时拒绝页' => str_contains($submitted['body'], DENY_DEADLINE_MARKER),
        '没有新增答卷' => countResponses($db, $survey['sid']) === $afterEntry,
        '答卷仍未提交' => $row !== null && $row['submitdate'] === null,
        '被拒绝的答案没有落库' => $row !== null && $row[$survey['quotaField']] === null,
    ]);
}

// -------------------------------------------------------------- C 硬配额

/**
 * 引擎自带配额的最后一个名额。
 *
 * 每一轮把限额调成"当前完成数 + 1"，只留一个名额，再让 N 个请求同时提交，
 * 数一数有几个人真的拿到了 submitdate。
 *
 * 竞态窗口是"第一个人写 submitdate 之前"，宽度取决于这批请求有多齐：并发数
 * 很高时 prefork 反而把请求排开了，窗口变窄。所以按并发档位扫一遍，每档两轮，
 * 取最大值——只要有一轮出现 2 人，就证明引擎配额没有原子性。
 */
function scenarioEngineQuotaLastSlot(PDO $db, array $survey, int $maxConcurrency): array
{
    $rounds = [];
    foreach (concurrencyLadder($maxConcurrency) as $concurrency) {
        for ($round = 0; $round < ENGINE_QUOTA_ROUNDS_PER_LEVEL; $round++) {
            $rounds[] = contestOneEngineSlot($db, $survey, $concurrency);
        }
    }
    $winners = array_column($rounds, 'lastSlotWinners');
    $everyRoundHadOneSlotLeft = !in_array(false, array_map(
        static fn(array $round): bool => $round['quotaLimit'] === $round['completionsBefore'] + 1,
        $rounds
    ), true);

    return result('引擎自带配额的最后一个名额（并发档位扫描）', [
        '每一轮开始时都只剩一个名额' => $everyRoundHadOneSlotLeft,
        '名额从不少发' => min($winners) >= 1,
    ], [
        'concurrencyLadder' => concurrencyLadder($maxConcurrency),
        'roundsPerLevel' => ENGINE_QUOTA_ROUNDS_PER_LEVEL,
        'rounds' => $rounds,
        'maxLastSlotWinners' => max($winners),
        'overAdmittingRounds' => count(array_filter($winners, static fn(int $w): bool => $w > 1)),
    ]);
}

/**
 * 一轮争抢：只留一个名额，N 个请求同时提交。
 *
 * @return array{concurrency: int, quotaLimit: int, completionsBefore: int, lastSlotWinners: int, quotaTerminationPages: int}
 */
function contestOneEngineSlot(PDO $db, array $survey, int $concurrency): array
{
    $before = countQuotaCompletions($db, $survey);
    setQuotaLimit($db, $survey['sid'], $before + 1);
    $sessions = prepareSessions($survey, $concurrency);
    $bodies = fireConcurrently($survey, $sessions);
    cleanupSessions($sessions);

    return [
        'concurrency' => $concurrency,
        'quotaLimit' => quotaLimit($db, $survey['sid']),
        'completionsBefore' => $before,
        'lastSlotWinners' => countQuotaCompletions($db, $survey) - $before,
        'quotaTerminationPages' => countMatching($bodies, ENGINE_QUOTA_MESSAGE),
    ];
}

/**
 * @return int[] 去重后的并发档位，最大不超过调用方给的上限
 */
function concurrencyLadder(int $maxConcurrency): array
{
    $ladder = array_filter([2, 4, 8, 16, 32, 64], static fn(int $level): bool => $level < $maxConcurrency);
    $ladder[] = $maxConcurrency;
    return array_values(array_unique($ladder));
}

/**
 * 把答卷表灌到生产量级再争抢同一个名额。
 *
 * `Quota::getCompleteCount()`（Quota.php:131）对答卷表做一次不加锁的全表
 * COUNT，答卷表上没有配额列的索引。答卷越多，COUNT 越慢，"先查后写"之间的
 * 窗口就越宽——在真实考试的数据量下，上面那种偶发超发会变成稳定超发。
 */
function scenarioEngineQuotaUnderLoad(PDO $db, array $survey, int $concurrency): array
{
    fillQuotaUpToLastSlot($survey, 1); // 成倍复制需要一行种子
    $paddingRows = inflateResponses($db, $survey, BULK_RESPONSE_ROWS);
    $countMillis = measureQuotaCountMillis($db, $survey);
    $rounds = [];
    for ($round = 0; $round < LOADED_QUOTA_ROUNDS; $round++) {
        $rounds[] = contestOneEngineSlot($db, $survey, $concurrency);
    }
    $winners = array_column($rounds, 'lastSlotWinners');

    return result('高答卷量下引擎配额的最后一个名额', [
        '答卷表已灌到目标量级' => $paddingRows >= BULK_RESPONSE_ROWS,
        '最后一个名额被发给了多个人' => max($winners) > 1,
    ], [
        'responseRows' => $paddingRows,
        'quotaCountMillis' => $countMillis,
        'concurrency' => $concurrency,
        'rounds' => $rounds,
        'maxLastSlotWinners' => max($winners),
        'overAdmittingRounds' => count(array_filter($winners, static fn(int $w): bool => $w > 1)),
    ]);
}

/**
 * 平台名额租约的最后一个名额。
 *
 * 每一轮把名额上限调成"当前有效租约数 + 1"，只留一个名额，再让 N 个请求同时
 * **进场**——租约是在 beforeSurveyPage 里领的，所以争抢发生在拿到题目之前。
 * 领到租约的人才看得见题目，其余人直接看到"名额已满"。
 *
 * 同一份问卷上仍然挂着引擎自带的配额（限额相同），它在这里从头到尾没机会
 * 发挥作用：进场阶段就已经把人数定死了，引擎配额只是第二道保险。
 */
function scenarioLeaseLastSlot(PDO $db, array $survey, int $concurrency): array
{
    $rounds = [];
    for ($round = 0; $round < LEASE_RACE_ROUNDS; $round++) {
        $rounds[] = contestOneLeaseSlot($db, $survey, $concurrency);
    }
    $granted = array_column($rounds, 'leasesGranted');
    $refused = array_column($rounds, 'refusedWithQuotaPage');

    return result('平台名额租约的最后一个名额', [
        '每一轮都只剩一个名额' => !in_array(false, array_map(
            static fn(array $round): bool => $round['slotLimit'] === $round['activeBefore'] + 1,
            $rounds
        ), true),
        '每一轮都只有一个人拿到名额' => max($granted) === 1 && min($granted) === 1,
        '其余人全部看到名额已满' => min($refused) === $concurrency - 1,
    ], [
        'concurrency' => $concurrency,
        'rounds' => $rounds,
        'maxLeasesGranted' => max($granted),
    ]);
}

/**
 * 一轮争抢：只留一个名额，N 个请求同时进场。
 *
 * @return array{slotLimit: int, activeBefore: int, leasesGranted: int, admitted: int, refusedWithQuotaPage: int}
 */
function contestOneLeaseSlot(PDO $db, array $survey, int $concurrency): array
{
    $activeBefore = countActiveLeases($db, $survey['sid']);
    setLeaseSlotLimit($db, $survey['sid'], $activeBefore + 1);

    $jars = [];
    $requests = [];
    for ($index = 0; $index < $concurrency; $index++) {
        $jars[$index] = tempnam(sys_get_temp_dir(), 'exam-race');
        $requests[$index] = ['url' => surveyUrl($survey['sid'], null), 'post' => null, 'jar' => $jars[$index]];
    }
    $bodies = httpMulti($requests);
    foreach ($jars as $jar) {
        @unlink($jar);
    }

    return [
        'slotLimit' => leaseSlotLimit($db, $survey['sid']),
        'activeBefore' => $activeBefore,
        'leasesGranted' => countActiveLeases($db, $survey['sid']) - $activeBefore,
        'admitted' => count(array_filter($bodies, static fn(string $body): bool => hasSurveyForm($body))),
        'refusedWithQuotaPage' => countMatching($bodies, DENY_QUOTA_MARKER),
    ];
}

/**
 * 只占位不交卷的人，租约到点自动把名额还回去。
 */
function scenarioAbandonedLeaseIsReturned(PDO $db, array $survey): array
{
    $abandoned = enterSurvey($survey, null);
    $blocked = runRespondent($survey, null);
    sleep(SHORT_LEASE_TTL_SECONDS + 2);
    $afterExpiry = runRespondent($survey, null);
    cleanupSession($abandoned);

    return result('过期租约归还名额', [
        '占位期间后来者被拒绝' => !$blocked['completed'] && str_contains($blocked['body'], DENY_QUOTA_MARKER),
        '租约过期后可以进场并完成' => $afterExpiry['completed'],
    ], ['leaseTtlSeconds' => SHORT_LEASE_TTL_SECONDS]);
}

// -------------------------------------------------------------- 观测点

function countResponses(PDO $db, int $surveyId): int
{
    return (int) $db->query('SELECT COUNT(*) FROM ' . table("responses_$surveyId"))->fetchColumn();
}

/**
 * @return array<string, mixed>
 */
function latestResponse(PDO $db, int $surveyId): array
{
    $row = $db->query('SELECT * FROM ' . table("responses_$surveyId") . ' ORDER BY id DESC LIMIT 1')
        ->fetch(PDO::FETCH_ASSOC);
    return $row === false ? [] : $row;
}

function countQuotaCompletions(PDO $db, array $survey): int
{
    $statement = $db->prepare(
        'SELECT COUNT(*) FROM ' . table("responses_{$survey['sid']}")
        . ' WHERE submitdate IS NOT NULL AND ' . quoteIdentifier($survey['quotaField']) . ' = ?'
    );
    $statement->execute([QUOTA_ANSWER_CODE]);
    return (int) $statement->fetchColumn();
}

/**
 * 某张准考证对应的答卷行（引擎在打开问卷时就会建好）。
 *
 * @return array<string, mixed>|null
 */
function responseForToken(PDO $db, array $survey, string $token): ?array
{
    $statement = $db->prepare(
        'SELECT * FROM ' . table("responses_{$survey['sid']}") . ' WHERE token = ? ORDER BY id DESC LIMIT 1'
    );
    $statement->execute([$token]);
    $row = $statement->fetch(PDO::FETCH_ASSOC);
    return $row === false ? null : $row;
}

/**
 * @param string[] $bodies
 */
function countMatching(array $bodies, string $needle): int
{
    return count(array_filter($bodies, static fn(string $body): bool => str_contains($body, $needle)));
}

/**
 * 用成倍复制把答卷表灌到目标行数。复制出来的行都答的是别的选项，不计入配额，
 * 只用来把 COUNT 拖慢。
 *
 * @return int 灌完之后的总行数
 */
function inflateResponses(PDO $db, array $survey, int $targetRows): int
{
    $table = table("responses_{$survey['sid']}");
    $column = quoteIdentifier($survey['quotaField']);
    $rows = (int) $db->query("SELECT COUNT(*) FROM $table")->fetchColumn();
    if ($rows === 0) {
        throw new RuntimeException("答卷表 $table 是空的，没法成倍复制");
    }
    while ($rows < $targetRows) {
        $db->exec(
            "INSERT INTO $table (submitdate, startlanguage, startdate, datestamp, $column)"
            . " SELECT submitdate, startlanguage, startdate, datestamp, " . $db->quote(PADDING_ANSWER_CODE)
            . " FROM (SELECT submitdate, startlanguage, startdate, datestamp FROM $table) AS snapshot"
        );
        $rows = (int) $db->query("SELECT COUNT(*) FROM $table")->fetchColumn();
    }
    return $rows;
}

/**
 * 引擎配额那条 COUNT 自己要跑多久（毫秒）。
 */
function measureQuotaCountMillis(PDO $db, array $survey): int
{
    $statement = $db->prepare(
        'SELECT COUNT(*) FROM ' . table("responses_{$survey['sid']}")
        . ' WHERE submitdate IS NOT NULL AND ' . quoteIdentifier($survey['quotaField']) . ' = ?'
    );
    $startedAt = microtime(true);
    $statement->execute([QUOTA_ANSWER_CODE]);
    $statement->fetchColumn();
    return (int) round((microtime(true) - $startedAt) * 1000);
}

function setQuotaLimit(PDO $db, int $surveyId, int $limit): void
{
    $db->prepare('UPDATE ' . table('quota') . ' SET qlimit = ? WHERE sid = ?')->execute([$limit, $surveyId]);
}

function quotaLimit(PDO $db, int $surveyId): int
{
    $statement = $db->prepare('SELECT qlimit FROM ' . table('quota') . ' WHERE sid = ?');
    $statement->execute([$surveyId]);
    return (int) $statement->fetchColumn();
}

/**
 * 当前仍然占着名额的租约数：已确认的，加上未过期的持有中租约。
 */
function countActiveLeases(PDO $db, int $surveyId): int
{
    $statement = $db->prepare(
        'SELECT COUNT(*) FROM ' . table('mjyruntimepolicy_lease')
        . " WHERE quota_key = ? AND (state = 'confirmed' OR (state = 'held' AND expires_at > UTC_TIMESTAMP()))"
    );
    $statement->execute(["survey:{$surveyId}"]);
    return (int) $statement->fetchColumn();
}

function setLeaseSlotLimit(PDO $db, int $surveyId, int $limit): void
{
    $db->prepare('UPDATE ' . table('mjyruntimepolicy_quota') . ' SET slot_limit = ? WHERE quota_key = ?')
        ->execute([$limit, "survey:{$surveyId}"]);
}

function leaseSlotLimit(PDO $db, int $surveyId): int
{
    $statement = $db->prepare('SELECT slot_limit FROM ' . table('mjyruntimepolicy_quota') . ' WHERE quota_key = ?');
    $statement->execute(["survey:{$surveyId}"]);
    return (int) $statement->fetchColumn();
}

function deadlineOf(PDO $db, int $surveyId, string $sessionKey): ?string
{
    $statement = $db->prepare(
        'SELECT deadline_at FROM ' . table('mjyruntimepolicy_deadline') . ' WHERE survey_id = ? AND session_key = ?'
    );
    $statement->execute([$surveyId, $sessionKey]);
    $value = $statement->fetchColumn();
    return $value === false ? null : substr((string) $value, 0, 19);
}

function isRecentUtc(string $value): bool
{
    if ($value === '') {
        return false;
    }
    $timestamp = strtotime($value . ' UTC');
    return $timestamp !== false && abs($timestamp - time()) < 3600;
}

function quoteIdentifier(string $name): string
{
    return '`' . str_replace('`', '``', $name) . '`';
}

/**
 * @param array<string, bool> $checks
 * @param array<string, mixed> $facts
 * @return array{scenario: string, passed: bool, checks: array<string, bool>, facts: array<string, mixed>}
 */
function result(string $scenario, array $checks, array $facts = []): array
{
    $passed = !in_array(false, $checks, true);
    $failedChecks = array_keys(array_filter($checks, static fn(bool $ok): bool => !$ok));
    info(($passed ? 'PASS ' : 'FAIL ') . $scenario . ($passed ? '' : ' — ' . implode('；', $failedChecks)));
    return ['scenario' => $scenario, 'passed' => $passed, 'checks' => $checks, 'facts' => $facts];
}

/**
 * 一个场景崩掉不该带走整轮证据：记成失败继续往下跑。
 *
 * @return array{scenario: string, passed: bool, checks: array<string, bool>, facts: array<string, mixed>}
 */
function runScenario(string $name, callable $scenario): array
{
    try {
        return $scenario();
    } catch (Throwable $exception) {
        return result($name, ['场景执行成功' => false], [
            'error' => get_class($exception) . ': ' . substr(strip_tags($exception->getMessage()), 0, 400),
        ]);
    }
}

function info(string $message): void
{
    fwrite(STDERR, "[exam] {$message}\n");
}
