<?php

namespace ls\tests;

/**
 * 监考：把判定结果接到考场记录与强制交卷上（WP-09.2）。
 *
 * 判定本身仍归 MjyAccessGate。这里只回答两个问题：
 *   1. 这次请求要不要记一笔考场记录、要不要把到点未交的卷强制交掉；
 *   2. 页面上该显示还剩多少秒——**由服务端算**。
 */
class MjyExamProctorTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const INSTANCE = 'exam-proctor-test';
    private const DEADLINE = '2026-09-24 10:30:00';
    private const BEFORE = '2026-09-24 10:00:00';
    private const AFTER = '2026-09-24 11:00:00';

    /** @var \MjyExamProctor */
    private $proctor;

    /** @var \MjyExamAttemptStore */
    private $attempts;

    /** @var int */
    private $examSurveyId;

    /** @var string */
    private $sessionKey;

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

    protected function setUp(): void
    {
        parent::setUp();
        $this->examSurveyId = random_int(942000, 942900);
        $this->sessionKey = 'token:' . bin2hex(random_bytes(8));
        $this->proctor = new \MjyExamProctor(\App()->getDb(), self::INSTANCE);
        $this->proctor->ensureSchema();
        $this->attempts = new \MjyExamAttemptStore(\App()->getDb(), self::INSTANCE);
        \App()->getDb()->createCommand()->createTable($this->responsesTable(), [
            'id' => 'pk',
            'submitdate' => 'datetime NULL',
        ]);
    }

    protected function tearDown(): void
    {
        // 考场记录不会自己消失，问卷号又是随机取的——不清干净的话，上一次跑留下的
        // 行会在下一次撞上同一个号时混进 expired()／reap() 的结果里。
        \App()->getDb()->createCommand()->delete(
            (new \MjyExamAttemptStore(\App()->getDb(), self::INSTANCE))->tableName(),
            'survey_id = :sid',
            [':sid' => $this->examSurveyId]
        );
        \App()->getDb()->createCommand()->dropTable($this->responsesTable());
        parent::tearDown();
    }

    private function responsesTable(): string
    {
        return \App()->getDb()->tablePrefix . 'responses_' . $this->examSurveyId;
    }

    private function insertResponse(): int
    {
        \App()->getDb()->createCommand()->insert($this->responsesTable(), ['submitdate' => null]);
        return (int) \App()->getDb()->createCommand()
            ->select('MAX(id)')->from($this->responsesTable())->queryScalar();
    }

    private function submitdateOf(int $responseId): ?string
    {
        $value = \App()->getDb()->createCommand()
            ->select('submitdate')->from($this->responsesTable())
            ->where('id = :id', [':id' => $responseId])->queryScalar();
        return $value === false || $value === null ? null : substr((string) $value, 0, 19);
    }

    // ------------------------------------------------------------ 没有限时的问卷

    public function testASurveyWithoutADeadlineIsNotProctored(): void
    {
        $timer = $this->proctor->onPage(
            $this->examSurveyId, $this->sessionKey, \MjyPolicyDecision::allow(), null, self::BEFORE);

        $this->assertNull($timer);
        $this->assertNull($this->attempts->find($this->examSurveyId, $this->sessionKey));
    }

    // ------------------------------------------------------------ 考试进行中

    public function testEnteringAnExamRecordsTheAttemptAndReportsTheRemainder(): void
    {
        $timer = $this->proctor->onPage(
            $this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), null, self::BEFORE);

        $this->assertSame(1800, $timer->remainingSeconds());
        $this->assertFalse($timer->isExpired());
        $this->assertSame(\MjyExamAttemptStore::STATE_IN_PROGRESS,
            $this->attempts->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    /**
     * 断线续考：同一个考场身份换个浏览器回来，剩余时间接着原来的算，
     * 不是重新给一整场。
     */
    public function testReconnectingResumesTheSameRemainder(): void
    {
        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), null, self::BEFORE);

        $later = $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), null, '2026-09-24 10:20:00');

        $this->assertSame(600, $later->remainingSeconds());
    }

    public function testTheResponseRowIsRememberedOnceItExists(): void
    {
        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), null, self::BEFORE);
        $responseId = $this->insertResponse();

        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), $responseId, self::BEFORE);

        $this->assertSame($responseId,
            $this->attempts->find($this->examSurveyId, $this->sessionKey)['response_id']);
    }

    // ------------------------------------------------------------ 到点

    /**
     * 要害：判定是"时间已到"时，**先把卷强制交掉再拒绝**。
     * 拒绝路径末尾是 App()->end()，顺序反了这份卷就永远交不上去。
     */
    public function testALateRequestForceSubmitsBeforeBeingDenied(): void
    {
        $responseId = $this->insertResponse();
        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), $responseId, self::BEFORE);

        $timer = $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::denyDuration(self::DEADLINE, 1800), $responseId, self::AFTER);

        $this->assertTrue($timer->isExpired());
        $this->assertSame(self::DEADLINE, $this->submitdateOf($responseId));
        $this->assertSame(\MjyExamAttemptStore::STATE_FORCED,
            $this->attempts->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    /** 没进过场的人到点来一下，不该凭空造出一份答卷。 */
    public function testALateRequestWithoutAnAttemptSubmitsNothing(): void
    {
        $timer = $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::denyDuration(self::DEADLINE, 1800), null, self::AFTER);

        $this->assertTrue($timer->isExpired());
        $attempt = $this->attempts->find($this->examSurveyId, $this->sessionKey);
        $this->assertNotNull($attempt);
        $this->assertSame(\MjyExamAttemptStore::STATE_FORCED, $attempt['state']);
    }

    // ------------------------------------------------------------ 正常交卷

    public function testCompletingTheExamSettlesTheAttempt(): void
    {
        $responseId = $this->insertResponse();
        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), $responseId, self::BEFORE);

        $this->proctor->onComplete($this->examSurveyId, $this->sessionKey, '2026-09-24 10:10:00');

        $this->assertSame(\MjyExamAttemptStore::STATE_SUBMITTED,
            $this->attempts->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    /** 按时交了卷的人，之后回收作业不该再去动那一行。 */
    public function testReapLeavesASubmittedAttemptAlone(): void
    {
        $responseId = $this->insertResponse();
        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), $responseId, self::BEFORE);
        $this->proctor->onComplete($this->examSurveyId, $this->sessionKey, '2026-09-24 10:10:00');

        $this->assertSame(0, $this->proctor->reap($this->examSurveyId, self::AFTER));
        $this->assertNull($this->submitdateOf($responseId));
    }

    /**
     * 直接关掉浏览器的人没有后续请求，beforeSurveyPage 挂不上——
     * 这条回收路径是他们唯一的兜底。
     */
    public function testReapForceSubmitsSomeoneWhoJustClosedTheBrowser(): void
    {
        $responseId = $this->insertResponse();
        $this->proctor->onPage($this->examSurveyId, $this->sessionKey,
            \MjyPolicyDecision::allow(self::DEADLINE), $responseId, self::BEFORE);

        $this->assertSame(1, $this->proctor->reap($this->examSurveyId, self::AFTER));
        $this->assertSame(self::DEADLINE, $this->submitdateOf($responseId));
    }
}
