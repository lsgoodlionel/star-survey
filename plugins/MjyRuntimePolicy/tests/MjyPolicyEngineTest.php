<?php

namespace ls\tests;

/**
 * P0-00.7 策略判定核心：把"这次请求能不能继续作答"收敛成一个纯服务端判定。
 *
 * 判定只读三样东西：平台下发的问卷策略、库里已定死的截止时刻、库里已发出的
 * 名额租约。请求里的任何字段（伪造的时间戳、伪造的隐藏域、旧 cookie）都不参与
 * 判定，所以客户端改时钟、刷新、重连、重开都不会改变结果。
 */
class MjyPolicyEngineTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const ENGINE_ID = 'exam-test';

    /** @var \MjyPolicyEngine */
    private $engine;

    /** @var int 每个用例一份独立的问卷号，避免上一轮留下的截止时刻与租约互相污染 */
    private $examSurveyId;

    /** @var string */
    private $sessionKey;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        // 加载插件会把插件目录加入引擎的类导入路径，测试才能直接 new 这些辅助类。
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
        $this->engine = new \MjyPolicyEngine(\App()->getDb(), self::ENGINE_ID);
        $this->engine->ensureSchema();
        $this->examSurveyId = random_int(800000, 899000) * 10;
        $this->sessionKey = 'session:' . bin2hex(random_bytes(8));
    }

    public function testSurveyWithoutPolicyIsAllowedThrough()
    {
        // Arrange：平台没有给这份问卷下发策略，插件必须完全透明。
        $surveyId = $this->examSurveyId + 9;

        // Act
        $decision = $this->engine->evaluateEntry($surveyId, $this->sessionKey, '2026-09-20 08:00:00');

        // Assert
        $this->assertTrue($decision->isAllowed());
    }

    public function testEntryBeforeDeadlineIsAllowedAndPinsTheDeadline()
    {
        // Arrange
        $this->engine->applyPolicy($this->examSurveyId, 1800, 0, 900);

        // Act
        $decision = $this->engine->evaluateEntry($this->examSurveyId, $this->sessionKey, '2026-09-20 08:00:00');

        // Assert
        $this->assertTrue($decision->isAllowed());
        $this->assertSame('2026-09-20 08:30:00', $decision->deadlineAt());
    }

    public function testRequestAfterTheDeadlineIsDeniedNoMatterHowItArrives()
    {
        // Arrange：08:00 进场，30 分钟时长。
        $this->engine->applyPolicy($this->examSurveyId, 1800, 0, 900);
        $this->engine->evaluateEntry($this->examSurveyId, $this->sessionKey, '2026-09-20 08:00:00');

        // Act：迟到的请求／旧会话重放／断线重连，都是同一个会话键在超时之后再来。
        $decision = $this->engine->evaluateEntry($this->examSurveyId, $this->sessionKey, '2026-09-20 08:30:01');

        // Assert
        $this->assertFalse($decision->isAllowed());
        $this->assertSame(\MjyPolicyDecision::REASON_DEADLINE, $decision->reason());
    }

    public function testReentryWithAFreshSessionCannotRestartTheClock()
    {
        // Arrange：同一个考生（同一张准考证 token）换浏览器重开。
        $this->engine->applyPolicy($this->examSurveyId, 1800, 0, 900);
        $examKey = 'token:CANDIDATE-1';
        $this->engine->evaluateEntry($this->examSurveyId, $examKey, '2026-09-20 08:00:00');

        // Act
        $decision = $this->engine->evaluateEntry($this->examSurveyId, $examKey, '2026-09-20 08:40:00');

        // Assert
        $this->assertFalse($decision->isAllowed());
        $this->assertSame('2026-09-20 08:30:00', $decision->deadlineAt());
    }

    public function testQuotaLeaseIsTakenAtEntryAndTheLastSlotGoesToOneSession()
    {
        // Arrange：只剩一个名额。
        $surveyId = $this->examSurveyId + 1;
        $this->engine->applyPolicy($surveyId, 0, 1, 900);

        // Act
        $first = $this->engine->evaluateEntry($surveyId, 'session:a', '2026-09-20 08:00:00');
        $second = $this->engine->evaluateEntry($surveyId, 'session:b', '2026-09-20 08:00:00');

        // Assert
        $this->assertTrue($first->isAllowed());
        $this->assertNotNull($first->leaseId());
        $this->assertFalse($second->isAllowed());
        $this->assertSame(\MjyPolicyDecision::REASON_QUOTA, $second->reason());
    }

    public function testAbandonedLeaseIsReturnedAfterItsTtl()
    {
        // Arrange
        $surveyId = $this->examSurveyId + 2;
        $this->engine->applyPolicy($surveyId, 0, 1, 60);
        $this->engine->evaluateEntry($surveyId, 'session:abandoner', '2026-09-20 08:00:00');

        // Act
        $decision = $this->engine->evaluateEntry($surveyId, 'session:next', '2026-09-20 08:05:00');

        // Assert
        $this->assertTrue($decision->isAllowed());
    }

    public function testConfirmedEntryKeepsItsSlotForever()
    {
        // Arrange
        $surveyId = $this->examSurveyId + 3;
        $this->engine->applyPolicy($surveyId, 0, 1, 60);
        $granted = $this->engine->evaluateEntry($surveyId, 'session:finisher', '2026-09-20 08:00:00');

        // Act：交卷把租约转成已确认，之后 TTL 到点也不归还。
        $this->engine->confirmEntry($surveyId, 'session:finisher', '2026-09-20 08:00:30');
        $decision = $this->engine->evaluateEntry($surveyId, 'session:late', '2026-09-20 08:05:00');

        // Assert
        $this->assertNotNull($granted->leaseId());
        $this->assertFalse($decision->isAllowed());
        $this->assertSame(\MjyPolicyDecision::REASON_QUOTA, $decision->reason());
    }

    public function testDeadlineIsCheckedBeforeTheQuotaSlotIsSpent()
    {
        // Arrange：超时的人不该再占掉一个名额。
        $surveyId = $this->examSurveyId + 4;
        $this->engine->applyPolicy($surveyId, 1800, 1, 900);
        $this->engine->evaluateEntry($surveyId, 'session:slowpoke', '2026-09-20 08:00:00');
        $this->engine->confirmEntry($surveyId, 'session:slowpoke', '2026-09-20 08:00:30');
        $this->engine->applyPolicy($surveyId, 1800, 2, 900);

        // Act
        $decision = $this->engine->evaluateEntry($surveyId, 'session:slowpoke', '2026-09-20 09:00:00');

        // Assert
        $this->assertFalse($decision->isAllowed());
        $this->assertSame(\MjyPolicyDecision::REASON_DEADLINE, $decision->reason());
    }

    public function testClientSuppliedTimestampsAreNotAnInputToTheDecision()
    {
        // Arrange：模拟被伪造的表单字段与请求参数。
        $this->engine->applyPolicy($this->examSurveyId, 1800, 0, 900);
        $this->engine->evaluateEntry($this->examSurveyId, $this->sessionKey, '2026-09-20 08:00:00');
        $_POST['MjyDeadline'] = '2099-01-01 00:00:00';
        $_POST['startdate'] = '2099-01-01 00:00:00';
        $_REQUEST['interviewtime'] = '0';

        try {
            // Act：判定只用服务端时钟，伪造字段不参与。
            $decision = $this->engine->evaluateEntry($this->examSurveyId, $this->sessionKey, '2026-09-20 08:30:01');

            // Assert
            $this->assertFalse($decision->isAllowed());
            $this->assertSame(\MjyPolicyDecision::REASON_DEADLINE, $decision->reason());
        } finally {
            unset($_POST['MjyDeadline'], $_POST['startdate'], $_REQUEST['interviewtime']);
        }
    }
}
