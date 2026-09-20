<?php

namespace ls\tests;

/**
 * P0-00.7 考试计时：每场考试的截止时刻必须由服务端一次性定死。
 *
 * 引擎只有问卷级的 `expires`（SurveyIndex.php:387，用 gmdate() 与全局到期时间
 * 比较），没有"每个作答会话各自倒计时"的概念；题目级 time_limit 只是前端 JS
 * 倒计时。这里把平台需要的契约钉死：首次进入时按服务端时钟写一次截止时刻，
 * 之后任何重开、重连、换浏览器都不得延长它。
 */
class MjyExamDeadlineStoreTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const SURVEY_ID = 770101;
    private const ENGINE_ID = 'exam-test';
    private const DURATION = 1800;

    /** @var \MjyExamDeadlineStore */
    private $store;

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
        $this->store = new \MjyExamDeadlineStore(\App()->getDb(), self::ENGINE_ID);
        $this->store->ensureSchema();
    }

    public function testServerClockReadsUtcFromTheDatabase()
    {
        // Arrange
        $clock = new \MjyServerClock(\App()->getDb());

        // Act
        $nowUtc = $clock->nowUtc();

        // Assert：与 PHP 侧的 UTC 差距必须在几秒内，格式与引擎写库一致。
        $this->assertMatchesRegularExpression('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/', $nowUtc);
        $this->assertLessThan(120, abs(strtotime($nowUtc . ' UTC') - time()));
    }

    public function testFirstEntryPinsDeadlineToStartPlusDuration()
    {
        // Arrange
        $sessionKey = $this->uniqueKey();
        $nowUtc = '2026-09-20 08:00:00';

        // Act
        $record = $this->store->start(self::SURVEY_ID, $sessionKey, self::DURATION, $nowUtc);

        // Assert
        $this->assertSame($nowUtc, $record['started_at']);
        $this->assertSame('2026-09-20 08:30:00', $record['deadline_at']);
        $this->assertSame(self::DURATION, (int) $record['duration_seconds']);
    }

    public function testReopeningTheSessionLaterDoesNotExtendTheDeadline()
    {
        // Arrange：首次进入时定死截止时刻。
        $sessionKey = $this->uniqueKey();
        $first = $this->store->start(self::SURVEY_ID, $sessionKey, self::DURATION, '2026-09-20 08:00:00');

        // Act：二十分钟后重开，并且谎报一个更长的时长。
        $second = $this->store->start(self::SURVEY_ID, $sessionKey, 36000, '2026-09-20 08:20:00');

        // Assert
        $this->assertSame($first['deadline_at'], $second['deadline_at']);
        $this->assertSame($first['started_at'], $second['started_at']);
        $this->assertSame(self::DURATION, (int) $second['duration_seconds']);
    }

    public function testConcurrentFirstEntryKeepsExactlyOneDeadline()
    {
        // Arrange：两个并发请求各拿到一个 store 实例。
        $sessionKey = $this->uniqueKey();
        $other = new \MjyExamDeadlineStore(\App()->getDb(), self::ENGINE_ID);

        // Act
        $winner = $this->store->start(self::SURVEY_ID, $sessionKey, self::DURATION, '2026-09-20 08:00:00');
        $loser = $other->start(self::SURVEY_ID, $sessionKey, self::DURATION, '2026-09-20 08:00:05');

        // Assert：败者采用胜者的截止时刻，表里只有一行。
        $this->assertSame($winner['deadline_at'], $loser['deadline_at']);
        $this->assertSame(1, $this->countRows($sessionKey));
    }

    public function testDeadlinesAreScopedPerSurveyAndPerSession()
    {
        // Arrange
        $keyA = $this->uniqueKey();
        $keyB = $this->uniqueKey();

        // Act
        $this->store->start(self::SURVEY_ID, $keyA, self::DURATION, '2026-09-20 08:00:00');
        $recordB = $this->store->start(self::SURVEY_ID, $keyB, self::DURATION, '2026-09-20 09:00:00');

        // Assert
        $this->assertSame('2026-09-20 09:30:00', $recordB['deadline_at']);
        $this->assertNull($this->store->find(self::SURVEY_ID + 1, $keyA));
    }

    public function testFindReturnsNullForAnUnknownSession()
    {
        $this->assertNull($this->store->find(self::SURVEY_ID, $this->uniqueKey()));
    }

    public function testStartRejectsANonPositiveDuration()
    {
        $this->expectException(\InvalidArgumentException::class);

        $this->store->start(self::SURVEY_ID, $this->uniqueKey(), 0, '2026-09-20 08:00:00');
    }

    private function uniqueKey(): string
    {
        return 'session:' . bin2hex(random_bytes(8));
    }

    private function countRows(string $sessionKey): int
    {
        return (int) \App()->getDb()->createCommand()
            ->select('COUNT(*)')
            ->from($this->store->tableName())
            ->where('session_key = :key', [':key' => $sessionKey])
            ->queryScalar();
    }
}
