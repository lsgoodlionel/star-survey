<?php

namespace ls\tests;

/**
 * WP-06.4：通道额度（ADR 0018 决定 7，契约 plugin-channel-v1）。
 *
 * 最要紧的一条是**懒建表**：独立安全审查发现，急着 ensureSchema() 会让每个匿名请求
 * 在验签之前跑一次真实的 schema 查询（Yii 的 getTable($name, true) 恒绕过缓存直接
 * loadTable()），正好推翻 ADR「验签之前零 IO」的立论，成为不需要密钥的放大面。
 * 现在只有 allow()——它在验签之后——才会碰表。
 */
class MjyChannelRateLimitTest extends TestBaseClass
{
    private const INSTANCE = 'hd-engine-01';
    private const SURVEY_ID = 991042;
    private const NOW = 1800000000;

    private function limit(int $limit = 3): \MjyChannelRateLimit
    {
        return new \MjyChannelRateLimit(\App()->getDb(), self::INSTANCE, $limit);
    }

    private function dropTable(): void
    {
        $table = $this->limit()->tableName();
        \App()->getDb()->createCommand()->setText('DROP TABLE IF EXISTS ' . $table)->execute();
        \App()->getDb()->getSchema()->refresh();
    }

    private function tableExists(): bool
    {
        return \App()->getDb()->getSchema()->getTable($this->limit()->tableName(), true) !== null;
    }

    protected function setUp(): void
    {
        parent::setUp();
        $this->dropTable();
    }

    public static function tearDownAfterClass(): void
    {
        // 别给同套件的其他用例留下半张表。
        $limit = new \MjyChannelRateLimit(\App()->getDb(), self::INSTANCE);
        $limit->ensureSchema();
        parent::tearDownAfterClass();
    }

    /**
     * 构造额度对象本身不得碰数据库。端点在**验签之前**就会构造它，
     * 一旦这里建表，未签名的请求就能逼出一次真实查询。
     */
    public function testConstructingTheLimiterTouchesNothing(): void
    {
        $this->limit();

        $this->assertFalse($this->tableExists());
    }

    public function testTheTableIsCreatedOnFirstUse(): void
    {
        $limit = $this->limit();

        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW));
        $this->assertTrue($this->tableExists());
    }

    public function testAllowsUpToTheLimitThenDenies(): void
    {
        $limit = $this->limit(3);

        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW));
        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW));
        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW));
        $this->assertFalse($limit->allow(self::SURVEY_ID, self::NOW));
    }

    public function testEachSurveyHasItsOwnBucket(): void
    {
        $limit = $this->limit(1);

        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW));
        $this->assertTrue($limit->allow(self::SURVEY_ID + 1, self::NOW));
    }

    public function testANewWindowStartsOver(): void
    {
        $limit = $this->limit(1);
        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW));
        $this->assertFalse($limit->allow(self::SURVEY_ID, self::NOW));

        $this->assertTrue($limit->allow(self::SURVEY_ID, self::NOW + \MjyChannelRateLimit::WINDOW_SECONDS));
    }

    public function testEnsureSchemaIsIdempotent(): void
    {
        $limit = $this->limit();
        $limit->ensureSchema();
        $limit->ensureSchema();

        $this->assertTrue($this->tableExists());
    }
}
