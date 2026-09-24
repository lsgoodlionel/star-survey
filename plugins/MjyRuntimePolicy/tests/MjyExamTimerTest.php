<?php

namespace ls\tests;

/**
 * 剩余时间的唯一算法（WP-09.2）。
 *
 * 要点只有一句：**剩余时间是服务端算出来的**。客户端时钟、请求里的任何时间字段
 * 都不是输入——它们连参数都不是，这个类根本收不到它们（ADR 0007 决定 1）。
 */
class MjyExamTimerTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const NOW = '2026-09-24 10:00:00';

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

    public function testRemainingIsTheGapToTheDeadline(): void
    {
        $timer = new \MjyExamTimer('2026-09-24 10:30:00', self::NOW);

        $this->assertSame(1800, $timer->remainingSeconds());
        $this->assertFalse($timer->isExpired());
    }

    public function testRemainingNeverGoesNegative(): void
    {
        $timer = new \MjyExamTimer('2026-09-24 09:00:00', self::NOW);

        $this->assertSame(0, $timer->remainingSeconds());
        $this->assertTrue($timer->isExpired());
    }

    /** 截止时刻本身已经不算剩余时间：区间是 [开始, 截止)。 */
    public function testTheDeadlineInstantIsAlreadyExpired(): void
    {
        $timer = new \MjyExamTimer(self::NOW, self::NOW);

        $this->assertSame(0, $timer->remainingSeconds());
        $this->assertTrue($timer->isExpired());
    }

    public function testOneSecondBeforeTheDeadlineIsStillRunning(): void
    {
        $timer = new \MjyExamTimer('2026-09-24 10:00:01', self::NOW);

        $this->assertSame(1, $timer->remainingSeconds());
        $this->assertFalse($timer->isExpired());
    }

    /**
     * 下发给页面的东西里只有"还剩多少秒"与服务端当下时刻，没有别的。
     * 尤其没有任何可以被改回去的"开始时刻＋时长"——那等于把计时交给客户端。
     */
    public function testWhatGoesToThePageIsOnlyTheRemainder(): void
    {
        $payload = (new \MjyExamTimer('2026-09-24 10:30:00', self::NOW))->toPayload();

        $this->assertSame(['remainingSeconds' => 1800, 'serverNow' => self::NOW, 'expired' => false], $payload);
    }

    public function testExpiredPayloadSaysSo(): void
    {
        $payload = (new \MjyExamTimer('2026-09-24 09:59:00', self::NOW))->toPayload();

        $this->assertSame(0, $payload['remainingSeconds']);
        $this->assertTrue($payload['expired']);
    }

    public function testAMalformedDeadlineIsAnError(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        new \MjyExamTimer('not a time', self::NOW);
    }
}
