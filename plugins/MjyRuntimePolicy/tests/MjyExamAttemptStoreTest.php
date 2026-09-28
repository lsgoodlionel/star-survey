<?php

namespace ls\tests;

/**
 * 考场记录与到点强制交卷（WP-09.2）。
 *
 * ADR 0007 的遗留项：「截止时刻只有『到点即拒』，没有『到点自动交卷』」。
 * 到点即拒对考试是错的——作答者已经写在答卷表里的答案会永远停在未交卷状态，
 * 等于白考一场。这里补上：到点由**服务端**把 submitdate 写进去。
 *
 * 写进去的时刻是**截止时刻**，不是"回收作业碰巧跑起来的时刻"。两个理由：
 * 作答者的时间就是在截止时刻用完的；而且这样结果与回收时机无关，可复现。
 */
class MjyExamAttemptStoreTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const INSTANCE = 'exam-attempt-test';
    private const DEADLINE = '2026-09-24 10:30:00';
    private const BEFORE = '2026-09-24 10:00:00';
    private const AFTER = '2026-09-24 11:00:00';

    /** @var \MjyExamAttemptStore */
    private $store;

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
        $this->examSurveyId = random_int(941000, 941900);
        $this->sessionKey = 'token:' . bin2hex(random_bytes(8));
        $this->store = new \MjyExamAttemptStore(\App()->getDb(), self::INSTANCE);
        $this->store->ensureSchema();
        $this->createResponsesTable();
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

    /** 引擎答卷表里与本用例有关的就这两列。 */
    private function createResponsesTable(): void
    {
        \App()->getDb()->createCommand()->createTable($this->responsesTable(), [
            'id' => 'pk',
            'submitdate' => 'datetime NULL',
        ]);
    }

    private function insertResponse(?string $submitdate = null): int
    {
        \App()->getDb()->createCommand()->insert($this->responsesTable(), ['submitdate' => $submitdate]);
        // 不用 getLastInsertID()：PostgreSQL 上它要序列名，两种库写法不同。
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

    // ------------------------------------------------------------ 记账

    public function testTrackRecordsTheAttemptWithItsDeadline(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);

        $attempt = $this->store->find($this->examSurveyId, $this->sessionKey);
        $this->assertSame(self::DEADLINE, $attempt['deadline_at']);
        $this->assertSame(\MjyExamAttemptStore::STATE_IN_PROGRESS, $attempt['state']);
        $this->assertNull($attempt['response_id']);
    }

    public function testTheResponseIdIsPickedUpWhenItFirstAppears(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);
        $responseId = $this->insertResponse();

        $this->store->track($this->examSurveyId, $this->sessionKey, $responseId, self::DEADLINE, self::BEFORE);

        $this->assertSame($responseId, $this->store->find($this->examSurveyId, $this->sessionKey)['response_id']);
    }

    /**
     * 断线重开时引擎会换一行答卷（newtest=Y 或新会话），答案在新的那一行里。
     * 考场记录要跟着走，否则强制交卷交的是一份空卷。
     */
    public function testTheAttemptFollowsTheRowTheCandidateIsFillingNow(): void
    {
        $first = $this->insertResponse();
        $second = $this->insertResponse();
        $this->store->track($this->examSurveyId, $this->sessionKey, $first, self::DEADLINE, self::BEFORE);

        $this->store->track($this->examSurveyId, $this->sessionKey, $second, self::DEADLINE, self::BEFORE);

        $this->assertSame($second, $this->store->find($this->examSurveyId, $this->sessionKey)['response_id']);
    }

    /** 没有答卷行号的那几次请求（第一页提交之前）不该把已知的行号抹掉。 */
    public function testATrackWithoutAResponseIdKeepsTheKnownOne(): void
    {
        $responseId = $this->insertResponse();
        $this->store->track($this->examSurveyId, $this->sessionKey, $responseId, self::DEADLINE, self::BEFORE);

        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);

        $this->assertSame($responseId, $this->store->find($this->examSurveyId, $this->sessionKey)['response_id']);
    }

    /**
     * 断线重连会再走一次 beforeSurveyPage。截止时刻**不能**因此往后挪，
     * 否则"断线一次多考半小时"就是可复现的作弊手法。
     */
    public function testReconnectingDoesNotMoveTheDeadline(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);

        $this->store->track($this->examSurveyId, $this->sessionKey, null, '2026-09-24 23:00:00', self::BEFORE);

        $this->assertSame(self::DEADLINE, $this->store->find($this->examSurveyId, $this->sessionKey)['deadline_at']);
    }

    public function testAttemptsAreScopedPerSurveyAndPerSession(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);
        $this->store->track($this->examSurveyId, 'token:someone-else', null, self::AFTER, self::BEFORE);

        $this->assertSame(self::DEADLINE, $this->store->find($this->examSurveyId, $this->sessionKey)['deadline_at']);
        $this->assertSame(self::AFTER, $this->store->find($this->examSurveyId, 'token:someone-else')['deadline_at']);
    }

    public function testMarkSubmittedSettlesTheAttempt(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);

        $this->assertTrue($this->store->markSubmitted($this->examSurveyId, $this->sessionKey, self::BEFORE));
        $this->assertSame(\MjyExamAttemptStore::STATE_SUBMITTED,
            $this->store->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    /** 已经交卷的人不能被再次"开考"——否则回收作业会去动一份已完成的答卷。 */
    public function testASettledAttemptIsNotReopenedByAnotherRequest(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);
        $this->store->markSubmitted($this->examSurveyId, $this->sessionKey, self::BEFORE);

        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::AFTER);

        $this->assertSame(\MjyExamAttemptStore::STATE_SUBMITTED,
            $this->store->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    // ------------------------------------------------------------ 强制交卷

    public function testForceSubmitWritesTheDeadlineAsTheSubmitTime(): void
    {
        $responseId = $this->insertResponse();
        $this->store->track($this->examSurveyId, $this->sessionKey, $responseId, self::DEADLINE, self::BEFORE);

        $this->assertTrue($this->store->forceSubmit($this->examSurveyId, $this->sessionKey, self::AFTER));

        $this->assertSame(self::DEADLINE, $this->submitdateOf($responseId));
        $this->assertSame(\MjyExamAttemptStore::STATE_FORCED,
            $this->store->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    public function testForceSubmitIsIdempotent(): void
    {
        $responseId = $this->insertResponse();
        $this->store->track($this->examSurveyId, $this->sessionKey, $responseId, self::DEADLINE, self::BEFORE);

        $this->assertTrue($this->store->forceSubmit($this->examSurveyId, $this->sessionKey, self::AFTER));
        $this->assertFalse($this->store->forceSubmit($this->examSurveyId, $this->sessionKey, self::AFTER));
        $this->assertSame(self::DEADLINE, $this->submitdateOf($responseId));
    }

    /** 人家自己按时交的卷，不能被强制交卷改掉 submitdate。 */
    public function testForceSubmitNeverOverwritesARealSubmission(): void
    {
        $realSubmit = '2026-09-24 10:15:00';
        $responseId = $this->insertResponse($realSubmit);
        $this->store->track($this->examSurveyId, $this->sessionKey, $responseId, self::DEADLINE, self::BEFORE);

        $this->store->forceSubmit($this->examSurveyId, $this->sessionKey, self::AFTER);

        $this->assertSame($realSubmit, $this->submitdateOf($responseId));
    }

    /** 一页都没提交过的人没有答卷行，强制交卷无事可做，但要把考场记录结掉。 */
    public function testAnAttemptWithoutAResponseRowIsStillSettled(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);

        $this->assertFalse($this->store->forceSubmit($this->examSurveyId, $this->sessionKey, self::AFTER));
        $this->assertSame(\MjyExamAttemptStore::STATE_FORCED,
            $this->store->find($this->examSurveyId, $this->sessionKey)['state']);
    }

    // ------------------------------------------------------------ 回收

    public function testExpiredFindsOnlyAttemptsPastTheirDeadline(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);
        $this->store->track($this->examSurveyId, 'token:still-running', null, '2026-09-25 10:00:00', self::BEFORE);

        $expired = $this->store->expired($this->examSurveyId, self::AFTER);

        $this->assertSame([$this->sessionKey], array_column($expired, 'session_key'));
    }

    public function testExpiredIgnoresSettledAttempts(): void
    {
        $this->store->track($this->examSurveyId, $this->sessionKey, null, self::DEADLINE, self::BEFORE);
        $this->store->markSubmitted($this->examSurveyId, $this->sessionKey, self::BEFORE);

        $this->assertSame([], $this->store->expired($this->examSurveyId, self::AFTER));
    }

    public function testReapForceSubmitsEveryExpiredAttempt(): void
    {
        $first = $this->insertResponse();
        $second = $this->insertResponse();
        $this->store->track($this->examSurveyId, $this->sessionKey, $first, self::DEADLINE, self::BEFORE);
        $this->store->track($this->examSurveyId, 'token:other', $second, self::DEADLINE, self::BEFORE);

        $this->assertSame(2, $this->store->reap($this->examSurveyId, self::AFTER));

        $this->assertSame(self::DEADLINE, $this->submitdateOf($first));
        $this->assertSame(self::DEADLINE, $this->submitdateOf($second));
        $this->assertSame(0, $this->store->reap($this->examSurveyId, self::AFTER));
    }
}
