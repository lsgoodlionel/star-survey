<?php

namespace ls\tests;

/**
 * 引擎侧的字典快照表（R02-03，ADR 0019）。
 *
 * 快照随 .lss 的 plugin_settings 下发一次，插件把它物化成两张带索引的表：
 * 之后每次判定路径、每次翻页都是索引查询，不再 json_decode 几十万字符。
 */
class MjyDictionaryStoreTest extends TestBaseClass
{
    private const DICTIONARY = 'cn-admin-divisions';
    private const VERSION = '2024.1';
    private const DIGEST = 'dg1:0123456789abcdef';

    /** @var \MjyDictionaryStore */
    private static $store;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$store = new \MjyDictionaryStore(\App()->getDb());
        self::$store->ensureSchema();
    }

    protected function setUp(): void
    {
        parent::setUp();
        self::$store->forget(self::DICTIONARY, self::VERSION);
        self::$store->forget(self::DICTIONARY, '2025.1');
    }

    /** 北京 / 广东两棵子树，与平台夹具同形。 */
    private function nodes(): array
    {
        return [
            ['110000', '', '北京市'],
            ['110100', '110000', '市辖区'],
            ['110101', '110100', '东城区'],
            ['110102', '110100', '西城区'],
            ['440000', '', '广东省'],
            ['440100', '440000', '广州市'],
            ['440103', '440100', '荔湾区'],
        ];
    }

    private function install(string $version = self::VERSION, string $digest = self::DIGEST, ?array $nodes = null): int
    {
        return self::$store->install(self::DICTIONARY, $version, $digest, $nodes ?? $this->nodes());
    }

    public function testInstallingMakesTheVersionAvailable()
    {
        $this->assertFalse(self::$store->isInstalled(self::DICTIONARY, self::VERSION, self::DIGEST));

        $this->assertSame(7, $this->install());

        $this->assertTrue(self::$store->isInstalled(self::DICTIONARY, self::VERSION, self::DIGEST));
    }

    /** 同一份快照装第二次什么都不做：每份答卷都重装一次就白做了。 */
    public function testInstallingTheSameSnapshotAgainIsANoOp()
    {
        $this->install();
        $this->assertSame(0, $this->install());
        $this->assertSame(7, self::$store->countNodes(self::DICTIONARY, self::VERSION));
    }

    /** 摘要变了就是另一份数据，整版换掉——绝不与旧节点混在一起。 */
    public function testADifferentDigestReplacesTheWholeVersion()
    {
        $this->install();
        $shrunk = [['110000', '', '北京市']];

        $this->assertSame(1, $this->install(self::VERSION, 'dg1:ffffffffffffffff', $shrunk));

        $this->assertSame(1, self::$store->countNodes(self::DICTIONARY, self::VERSION));
        $this->assertFalse(self::$store->isInstalled(self::DICTIONARY, self::VERSION, self::DIGEST));
        $this->assertTrue(self::$store->isInstalled(self::DICTIONARY, self::VERSION, 'dg1:ffffffffffffffff'));
    }

    public function testChildrenComeBackOneLevelAtATimeInTheDeclaredOrder()
    {
        $this->install();

        $roots = self::$store->children(self::DICTIONARY, self::VERSION, null, 0, 10);
        $this->assertSame(['110000', '440000'], array_column($roots, 'code'));
        $this->assertSame(['北京市', '广东省'], array_column($roots, 'label'));
        $this->assertSame(2, self::$store->countChildren(self::DICTIONARY, self::VERSION, null));

        $districts = self::$store->children(self::DICTIONARY, self::VERSION, '110100', 0, 10);
        $this->assertSame(['110101', '110102'], array_column($districts, 'code'));
    }

    public function testAPageIsAPage()
    {
        $this->install();

        $first = self::$store->children(self::DICTIONARY, self::VERSION, '110100', 0, 1);
        $second = self::$store->children(self::DICTIONARY, self::VERSION, '110100', 1, 1);

        $this->assertSame(['110101'], array_column($first, 'code'));
        $this->assertSame(['110102'], array_column($second, 'code'));
    }

    public function testALeafHasNoChildren()
    {
        $this->install();
        $this->assertSame([], self::$store->children(self::DICTIONARY, self::VERSION, '110101', 0, 10));
    }

    public function testSearchMatchesTheLabelOrTheCode()
    {
        $this->install();

        $this->assertSame(['440103'], array_column(
            self::$store->search(self::DICTIONARY, self::VERSION, '荔湾', 10), 'code'));
        $this->assertSame(['110102'], array_column(
            self::$store->search(self::DICTIONARY, self::VERSION, '110102', 10), 'code'));
        $this->assertSame([], self::$store->search(self::DICTIONARY, self::VERSION, '不存在的地方', 10));
    }

    public function testSearchNeverReturnsMoreThanItWasAskedFor()
    {
        $this->install();
        $this->assertCount(2, self::$store->search(self::DICTIONARY, self::VERSION, '区', 2));
    }

    /** 路径判定只需要这几个代码，一次取回来，不逐级往返。 */
    public function testNodesInReturnsOnlyTheAskedForCodesOfThatVersion()
    {
        $this->install();
        $this->install('2025.1', 'dg1:aaaaaaaaaaaaaaaa', [['990000', '', '新省']]);

        $found = self::$store->nodesIn(self::DICTIONARY, self::VERSION, ['110000', '990000', 'nope']);

        // 按键存在性断言而不是比 array_keys()：PHP 会把“110000”这种规范整数字符串键
        // 自动转成 int（本用例首跑就是被这一点抦红的）。取用方 checkOnePath 用的正是
        // isset($found[$code])，PHP 对查询键做同样的转换，所以键的内部表示不是契约的一部分。
        $this->assertCount(1, $found);
        $this->assertTrue(isset($found['110000']));
        $this->assertFalse(isset($found['990000']), '别的版本的节点不得串过来');
        $this->assertFalse(isset($found['nope']));
        $this->assertSame(1, (int) $found['110000']['depth']);
        $this->assertSame('', (string) $found['110000']['parent_code']);
    }

    public function testTwoVersionsOfTheSameDictionaryDoNotSeeEachOther()
    {
        $this->install();
        $this->install('2025.1', 'dg1:aaaaaaaaaaaaaaaa', [['990000', '', '新省']]);

        $this->assertSame(['990000'], array_column(
            self::$store->children(self::DICTIONARY, '2025.1', null, 0, 10), 'code'));
        $this->assertSame(['110000', '440000'], array_column(
            self::$store->children(self::DICTIONARY, self::VERSION, null, 0, 10), 'code'));
    }

    /** 装进来的快照自己就不成立时宁可整版不装，也不能装半棵树。 */
    public function testASnapshotWithADanglingParentIsRefused()
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->install(self::VERSION, self::DIGEST, [['110101', '999999', '东城区']]);
    }

    public function testASnapshotWithDuplicateCodesIsRefused()
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->install(self::VERSION, self::DIGEST, [['110000', '', '北京市'], ['110000', '', '又一个']]);
    }

    public function testEnsureSchemaIsIdempotent()
    {
        self::$store->ensureSchema();
        self::$store->ensureSchema();
        $this->install();
        $this->assertSame(7, self::$store->countNodes(self::DICTIONARY, self::VERSION));
    }
}
