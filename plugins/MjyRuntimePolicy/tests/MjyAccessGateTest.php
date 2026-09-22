<?php

namespace ls\tests;

/**
 * WP-04.1 / 04.2 访问闸门：时间窗、IP、地区、密码、按身份限次、作答时长。
 *
 * 判定的输入只有：平台下发的策略、数据库里的租约与截止时刻、服务端取到的客户端
 * 身份（token／设备 Cookie／IP）。"现在"只来自服务端时钟（这里由测试显式给定）。
 */
class MjyAccessGateTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const ENGINE_ID = 'access-test';
    private const OPENS = '2026-10-01 01:00:00';
    private const CLOSES = '2026-10-07 10:30:00';
    private const DURING = '2026-10-02 08:00:00';

    /** @var \MjyAccessGate */
    private $gate;

    /** @var int */
    private $policySurveyId;

    /** @var \MjyQuotaLeaseStore */
    private $leases;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        $record = self::installAndActivatePlugin(self::PLUGIN_NAME);
        \App()->getPluginManager()->loadPlugin(self::PLUGIN_NAME, $record->id);
    }

    public static function tearDownAfterClass(): void
    {
        self::deActivatePlugin(self::PLUGIN_NAME);
        parent::tearDownAfterClass();
    }

    public function setUp(): void
    {
        parent::setUp();
        $db = \App()->getDb();
        (new \MjyPolicyEngine($db, self::ENGINE_ID))->ensureSchema();
        // 测试用的地区数据源：固定映射，查不到返回 null。运行时才声明，插件目录此时已导入。
        $regions = new class (['1.0.2.9' => 'CN-BJ', '8.8.8.8' => 'US']) implements \MjyRegionResolver {
            /** @var array<string, string> */
            private $regions;

            public function __construct(array $regions)
            {
                $this->regions = $regions;
            }

            public function resolve(string $ip): ?string
            {
                return $this->regions[$ip] ?? null;
            }
        };
        $this->gate = new \MjyAccessGate($db, self::ENGINE_ID, $regions);
        $this->leases = new \MjyQuotaLeaseStore($db);
        // 每个用例一份独立问卷号，互不污染租约与截止时刻。
        $this->policySurveyId = random_int(500000, 599999);
    }

    // ------------------------------------------------------------ 时间窗

    public function testBeforeTheWindowOpensIsDenied()
    {
        $decision = $this->evaluate($this->policy(['window' => $this->window()]), $this->request(), '2026-10-01 00:59:59');

        $this->assertSame(\MjyPolicyDecision::REASON_NOT_OPEN, $decision->reason());
        $this->assertStringContainsString('2026-10-01 09:00（Asia/Shanghai）', $decision->message());
    }

    public function testInsideTheWindowIsAllowed()
    {
        $policy = $this->policy(['window' => $this->window()]);

        $this->assertTrue($this->evaluate($policy, $this->request(), self::OPENS)->isAllowed());
        $this->assertTrue($this->evaluate($policy, $this->request(), '2026-10-07 10:29:59')->isAllowed());
    }

    public function testClosingInstantIsExclusive()
    {
        $decision = $this->evaluate($this->policy(['window' => $this->window()]), $this->request(), self::CLOSES);

        $this->assertSame(\MjyPolicyDecision::REASON_CLOSED, $decision->reason());
        $this->assertStringContainsString('2026-10-07 18:30（Asia/Shanghai）', $decision->message());
    }

    public function testClientSuppliedTimesAreNotAnInput()
    {
        $_POST['startdate'] = '2026-10-02 00:00:00';
        $_SERVER['HTTP_DATE'] = 'Fri, 02 Oct 2026 00:00:00 GMT';
        try {
            $decision = $this->evaluate($this->policy(['window' => $this->window()]), $this->request(), '2026-10-08 00:00:00');
            $this->assertSame(\MjyPolicyDecision::REASON_CLOSED, $decision->reason());
        } finally {
            unset($_POST['startdate'], $_SERVER['HTTP_DATE']);
        }
    }

    // ------------------------------------------------------------ IP 与地区

    public function testDeniedIpIsRejectedEvenInsideAnAllowedRange()
    {
        $policy = $this->policy(['network' => $this->network(['allowIps' => ['10.0.0.0/8'], 'denyIps' => ['10.9.9.9/32']])]);

        $this->assertSame(\MjyPolicyDecision::REASON_NETWORK, $this->evaluate($policy, $this->request(['ip' => '10.9.9.9']))->reason());
        $this->assertTrue($this->evaluate($policy, $this->request(['ip' => '10.1.1.1']))->isAllowed());
        $this->assertSame(\MjyPolicyDecision::REASON_NETWORK, $this->evaluate($policy, $this->request(['ip' => '11.1.1.1']))->reason());
    }

    public function testRegionAllowListUsesTheResolver()
    {
        $policy = $this->policy(['network' => $this->network(['allowRegions' => ['CN']])]);

        $this->assertTrue($this->evaluate($policy, $this->request(['ip' => '1.0.2.9']))->isAllowed());
        $this->assertSame(\MjyPolicyDecision::REASON_REGION, $this->evaluate($policy, $this->request(['ip' => '8.8.8.8']))->reason());
    }

    public function testUnknownRegionFollowsTheConfiguredFallback()
    {
        $deny = $this->policy(['network' => $this->network(['allowRegions' => ['CN']])]);
        $allow = $this->policy(['network' => $this->network(['allowRegions' => ['CN'], 'regionUnknown' => 'allow'])]);

        $this->assertSame(\MjyPolicyDecision::REASON_REGION_UNKNOWN, $this->evaluate($deny, $this->request(['ip' => '9.9.9.9']))->reason());
        $this->assertTrue($this->evaluate($allow, $this->request(['ip' => '9.9.9.9']))->isAllowed());
    }

    public function testDenyRegionWins()
    {
        $policy = $this->policy(['network' => $this->network(['denyRegions' => ['US']])]);

        $this->assertSame(\MjyPolicyDecision::REASON_REGION, $this->evaluate($policy, $this->request(['ip' => '8.8.8.8']))->reason());
        $this->assertTrue($this->evaluate($policy, $this->request(['ip' => '1.0.2.9']))->isAllowed());
    }

    // ------------------------------------------------------------ 密码

    public function testPasswordIsRequiredUntilTheSessionIsUnlocked()
    {
        $policy = $this->policy(['passwordHash' => 'pbkdf2-sha256$100000$MDEyMzQ1Njc4OWFiY2RlZg==$3FoEFG2OYy4tuwfB20lqThl5uly72sspE2cf0L6qdp4=']);

        $this->assertSame(\MjyPolicyDecision::REASON_PASSWORD, $this->evaluate($policy, $this->request())->reason());
        $this->assertTrue($this->evaluate($policy, $this->request(['unlocked' => true]))->isAllowed());
    }

    public function testWindowIsCheckedBeforeAskingForThePassword()
    {
        $policy = $this->policy(['window' => $this->window(), 'passwordHash' => 'pbkdf2-sha256$100000$MDEyMzQ1Njc4OWFiY2RlZg==$3FoEFG2OYy4tuwfB20lqThl5uly72sspE2cf0L6qdp4=']);

        $this->assertSame(\MjyPolicyDecision::REASON_CLOSED, $this->evaluate($policy, $this->request(), '2026-12-01 00:00:00')->reason());
    }

    // ------------------------------------------------------------ 限次

    public function testDeviceMayRespondOnceAndReentryIsTheSameAttempt()
    {
        $policy = $this->policy(['responses' => [['by' => 'device', 'max' => 1]]]);
        $device = $this->request(['device' => 'dev-1']);

        $this->assertTrue($this->evaluate($policy, $device)->isAllowed());
        $this->assertTrue($this->evaluate($policy, $device)->isAllowed(), '刷新／翻页是同一次作答');
        $this->assertSame(1, $this->gate->confirm($policy, $device, self::DURING));

        $again = $this->evaluate($policy, $device);
        $this->assertSame(\MjyPolicyDecision::REASON_ATTEMPTS, $again->reason());
        $this->assertStringContainsString('1 次', $again->message());
        $this->assertTrue($this->evaluate($policy, $this->request(['device' => 'dev-2']))->isAllowed());
    }

    public function testCombinedDimensionsEachHold()
    {
        // 每个 token 一次、每个 IP 两次：同一出口的第三个人被拒，且不白占他自己的 token 名额。
        $policy = $this->policy(['responses' => [['by' => 'token', 'max' => 1], ['by' => 'ip', 'max' => 2]]]);
        foreach (['tok-a', 'tok-b'] as $token) {
            $request = $this->request(['token' => $token, 'ip' => '203.0.113.5']);
            $this->assertTrue($this->evaluate($policy, $request)->isAllowed());
            $this->gate->confirm($policy, $request, self::DURING);
        }

        $third = $this->evaluate($policy, $this->request(['token' => 'tok-c', 'ip' => '203.0.113.5']));

        $this->assertSame(\MjyPolicyDecision::REASON_ATTEMPTS, $third->reason());
        $this->assertSame(0, $this->leases->usedSlots("person:{$this->policySurveyId}:token:tok-c", self::DURING));
        $this->assertTrue($this->evaluate($policy, $this->request(['token' => 'tok-c', 'ip' => '198.51.100.1']))->isAllowed());
    }

    public function testAbandonedAttemptReturnsItsSlotAfterTheTtl()
    {
        $policy = $this->policy(['responses' => [['by' => 'ip', 'max' => 1]]]);
        $this->evaluate($policy, $this->request(['device' => 'dev-1']), self::DURING);

        $blocked = $this->evaluate($policy, $this->request(['device' => 'dev-2']), '2026-10-02 08:10:00');
        $later = $this->evaluate($policy, $this->request(['device' => 'dev-2']), '2026-10-03 08:00:01');

        // 同一 IP 上另一个设备：名额被占着时拒绝，租约 24 小时到期后归还。
        $this->assertSame(\MjyPolicyDecision::REASON_ATTEMPTS, $blocked->reason());
        $this->assertTrue($later->isAllowed());
    }

    public function testTokenDimensionWithoutATokenDefersToTheEngineTokenGate()
    {
        $policy = $this->policy(['responses' => [['by' => 'token', 'max' => 1]]]);

        $this->assertTrue($this->evaluate($policy, $this->request(['isClosedAccess' => true]))->isAllowed());
        $this->assertSame(
            \MjyPolicyDecision::REASON_TOKEN_REQUIRED,
            $this->evaluate($policy, $this->request(['isClosedAccess' => false]))->reason()
        );
    }

    // ------------------------------------------------------------ 时长

    public function testDurationIsPinnedAtFirstEntryAndEnforcedOnTheServer()
    {
        $policy = $this->policy(['maxDurationSeconds' => 600]);
        $token = $this->request(['token' => 'tok-a']);

        $this->assertTrue($this->evaluate($policy, $token, '2026-10-02 08:00:00')->isAllowed());
        $this->assertTrue($this->evaluate($policy, $token, '2026-10-02 08:10:00')->isAllowed());
        $late = $this->evaluate($policy, $token, '2026-10-02 08:10:01');

        $this->assertSame(\MjyPolicyDecision::REASON_DURATION, $late->reason());
        $this->assertStringContainsString('10 分钟', $late->message());
    }

    public function testNewBrowserWithTheSameTokenCannotRestartTheClock()
    {
        $policy = $this->policy(['maxDurationSeconds' => 600]);
        $this->evaluate($policy, $this->request(['token' => 'tok-a', 'device' => 'browser-1']), '2026-10-02 08:00:00');

        $decision = $this->evaluate($policy, $this->request(['token' => 'tok-a', 'device' => 'browser-2']), '2026-10-02 08:20:00');

        $this->assertSame(\MjyPolicyDecision::REASON_DURATION, $decision->reason());
    }

    public function testCompletedAttemptLetsTheNextOneStartAFreshClock()
    {
        $policy = $this->policy(['maxDurationSeconds' => 600, 'responses' => [['by' => 'device', 'max' => 2]]]);
        $device = $this->request(['device' => 'dev-1']);
        $this->evaluate($policy, $device, '2026-10-02 08:00:00');
        $this->gate->confirm($policy, $device, '2026-10-02 08:05:00');

        $second = $this->evaluate($policy, $device, '2026-10-02 09:00:00');

        $this->assertTrue($second->isAllowed());
        $this->assertSame('2026-10-02 09:10:00', $second->deadlineAt());
    }

    public function testTokensAreNotInterchangeableAcrossSurveys()
    {
        // 续答令牌不互用：A 卷里用掉的次数与计时不影响 B 卷。
        $policy = $this->policy(['maxDurationSeconds' => 600, 'responses' => [['by' => 'token', 'max' => 1]]]);
        $surveyA = $this->request(['token' => 'shared']);
        $this->evaluate($policy, $surveyA, '2026-10-02 08:00:00');
        $this->gate->confirm($policy, $surveyA, '2026-10-02 08:01:00');

        $surveyB = $this->request(['token' => 'shared', 'surveyId' => $this->policySurveyId + 1]);
        $decision = $this->evaluate($policy, $surveyB, '2026-10-02 09:00:00');

        $this->assertTrue($decision->isAllowed());
        $this->assertSame('2026-10-02 09:10:00', $decision->deadlineAt());
    }

    public function testConfirmWithoutAnAttemptIsHarmless()
    {
        $policy = $this->policy(['responses' => [['by' => 'device', 'max' => 1]]]);

        $this->assertSame(0, $this->gate->confirm($policy, $this->request(['device' => 'never-entered']), self::DURING));
    }

    // ------------------------------------------------------------ 零件

    private function evaluate(\MjyAccessPolicy $policy, \MjyAccessRequest $request, string $now = self::DURING): \MjyPolicyDecision
    {
        return $this->gate->evaluate($policy, $request, $now);
    }

    private function policy(array $overrides): \MjyAccessPolicy
    {
        $payload = array_merge([
            'schema' => 'mjy-access-policy/1',
            'window' => null,
            'passwordHash' => null,
            'responses' => [],
            'maxDurationSeconds' => null,
            'network' => null,
        ], $overrides);
        return \MjyAccessPolicy::fromJson(json_encode($payload));
    }

    private function window(): array
    {
        return [
            'opensAt' => self::OPENS,
            'closesAt' => self::CLOSES,
            'timezone' => 'Asia/Shanghai',
            'opensAtLocal' => '2026-10-01T09:00',
            'closesAtLocal' => '2026-10-07T18:30',
        ];
    }

    private function network(array $overrides): array
    {
        return array_merge(
            ['allowIps' => [], 'denyIps' => [], 'allowRegions' => [], 'denyRegions' => [], 'regionUnknown' => 'deny'],
            $overrides
        );
    }

    private function request(array $overrides = []): \MjyAccessRequest
    {
        $values = array_merge([
            'surveyId' => $this->policySurveyId,
            'ip' => '203.0.113.10',
            'token' => null,
            'device' => 'device-default',
            'unlocked' => false,
            'isClosedAccess' => false,
        ], $overrides);
        return new \MjyAccessRequest(
            $values['surveyId'],
            $values['ip'],
            $values['token'],
            $values['device'],
            $values['unlocked'],
            $values['isClosedAccess']
        );
    }
}
