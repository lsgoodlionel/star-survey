<?php

namespace ls\tests;

/**
 * P0-00.7 硬配额：最后一个名额只能给一个人。
 *
 * 引擎自带的配额是"先查后写"：Quota::getCompleteCount()（Quota.php:131）对答卷表
 * 做一次不加锁的 COUNT，Quotas::checkCompletedQuota()（Quotas.php:518）拿这个数
 * 与 qlimit 比较，随后 em_manager_helper.php:5435 才写 submitdate——三步之间没有
 * 事务也没有行锁。并发下它会放行多于剩余名额的人（端到端实测见证据文档）。
 *
 * 平台侧的名额租约把"占位"提前到进入考场之前，并且用配额行上的排它锁把并发
 * 请求串行化：拿到租约才允许作答，租约过期自动归还，完成时转为已确认。
 */
class MjyQuotaLeaseStoreTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const SURVEY_ID = 770201;
    private const TTL = 900;
    private const NOW = '2026-09-20 08:00:00';

    /** @var \MjyQuotaLeaseStore */
    private $store;

    /** @var string */
    private $quotaKey;

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
        $this->store = new \MjyQuotaLeaseStore(\App()->getDb());
        $this->store->ensureSchema();
        $this->quotaKey = 'quota:' . bin2hex(random_bytes(8));
    }

    public function testReserveGrantsSlotsUpToTheLimitAndThenRefuses()
    {
        // Arrange
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 2);

        // Act
        $first = $this->store->reserve($this->quotaKey, 'holder-1', self::TTL, self::NOW);
        $second = $this->store->reserve($this->quotaKey, 'holder-2', self::TTL, self::NOW);
        $third = $this->store->reserve($this->quotaKey, 'holder-3', self::TTL, self::NOW);

        // Assert
        $this->assertNotNull($first);
        $this->assertNotNull($second);
        $this->assertNull($third);
        $this->assertSame(2, $this->store->usedSlots($this->quotaKey, self::NOW));
    }

    public function testReserveIsIdempotentForTheSameHolder()
    {
        // Arrange：同一个作答者每翻一页都会再次触发 beforeSurveyPage。
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 1);
        $first = $this->store->reserve($this->quotaKey, 'holder-1', self::TTL, self::NOW);

        // Act
        $again = $this->store->reserve($this->quotaKey, 'holder-1', self::TTL, self::NOW);

        // Assert：复用同一张租约，不会吃掉第二个名额。
        $this->assertSame($first['lease_id'], $again['lease_id']);
        $this->assertSame(1, $this->store->usedSlots($this->quotaKey, self::NOW));
    }

    public function testExpiredLeaseReturnsTheSlotToTheNextRespondent()
    {
        // Arrange：只剩一个名额，被人占住但没有完成。
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 1);
        $this->store->reserve($this->quotaKey, 'abandoner', 60, self::NOW);
        $afterExpiry = '2026-09-20 08:05:00';

        // Act
        $late = $this->store->reserve($this->quotaKey, 'next-in-line', self::TTL, $afterExpiry);

        // Assert
        $this->assertNotNull($late);
        $this->assertSame(1, $this->store->usedSlots($this->quotaKey, $afterExpiry));
    }

    public function testConfirmedLeaseKeepsTheSlotAfterItsTtl()
    {
        // Arrange：交卷之后名额必须永久落定，不能因为 TTL 到点被人抢走。
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 1);
        $lease = $this->store->reserve($this->quotaKey, 'finisher', 60, self::NOW);
        $this->store->confirm($lease['lease_id']);
        $afterExpiry = '2026-09-20 08:05:00';

        // Act
        $late = $this->store->reserve($this->quotaKey, 'late-comer', self::TTL, $afterExpiry);

        // Assert
        $this->assertNull($late);
        $this->assertSame(1, $this->store->usedSlots($this->quotaKey, $afterExpiry));
    }

    public function testReleasedLeaseFreesTheSlotImmediately()
    {
        // Arrange
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 1);
        $lease = $this->store->reserve($this->quotaKey, 'quitter', self::TTL, self::NOW);

        // Act
        $released = $this->store->release($lease['lease_id']);

        // Assert
        $this->assertTrue($released);
        $this->assertSame(0, $this->store->usedSlots($this->quotaKey, self::NOW));
        $this->assertNotNull($this->store->reserve($this->quotaKey, 'next', self::TTL, self::NOW));
    }

    public function testReapMarksExpiredLeasesWithoutTouchingConfirmedOnes()
    {
        // Arrange
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 3);
        $abandoned = $this->store->reserve($this->quotaKey, 'abandoner', 60, self::NOW);
        $finished = $this->store->reserve($this->quotaKey, 'finisher', 60, self::NOW);
        $this->store->confirm($finished['lease_id']);
        $afterExpiry = '2026-09-20 08:05:00';

        // Act
        $reaped = $this->store->reap($this->quotaKey, $afterExpiry);

        // Assert
        $this->assertSame(1, $reaped);
        $this->assertSame(\MjyQuotaLeaseStore::STATE_EXPIRED, $this->store->findLease($abandoned['lease_id'])['state']);
        $this->assertSame(\MjyQuotaLeaseStore::STATE_CONFIRMED, $this->store->findLease($finished['lease_id'])['state']);
        $this->assertSame(0, $this->store->reap($this->quotaKey, $afterExpiry));
    }

    public function testReserveRefusesAnUndefinedQuota()
    {
        $this->expectException(\RuntimeException::class);

        $this->store->reserve('quota:never-defined', 'holder-1', self::TTL, self::NOW);
    }

    public function testDefineQuotaUpdatesTheLimitWithoutLosingLeases()
    {
        // Arrange
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 1);
        $this->store->reserve($this->quotaKey, 'holder-1', self::TTL, self::NOW);

        // Act：平台把名额从 1 调到 2。
        $this->store->defineQuota($this->quotaKey, self::SURVEY_ID, 2);

        // Assert
        $this->assertSame(1, $this->store->usedSlots($this->quotaKey, self::NOW));
        $this->assertNotNull($this->store->reserve($this->quotaKey, 'holder-2', self::TTL, self::NOW));
    }
}
