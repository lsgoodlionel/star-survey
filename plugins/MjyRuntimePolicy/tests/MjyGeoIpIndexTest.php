<?php

namespace ls\tests;

/**
 * 离线地区数据源的预排序索引（ADR 0016 缺口「逐行扫描」的收口）。
 *
 * 要钉住两件事：
 *   1. **答案不变**——换成二分查找之后，同一份 CSV 对同一个 IP 给出的地区码与逐行扫描时一致；
 *   2. **确实是二分**——索引建好之后查一次不再读 CSV（把 CSV 删掉照样查得到）。
 *
 * 第 2 条是这次改动的全部意义。只断言答案对的话，把实现换回逐行扫描测试照样绿。
 */
class MjyGeoIpIndexTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';

    /** @var string[] 本次测试建过的临时文件，tearDown 逐个删掉。 */
    private $scratch = [];

    /** 插件类由插件加载器导入；不自己装一次，单独跑本文件时根本找不到 MjyCsvRegionResolver。 */
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

    protected function tearDown(): void
    {
        foreach ($this->scratch as $path) {
            @unlink($path);
            @unlink(\MjyGeoIpIndex::pathFor($path));
        }
        $this->scratch = [];
        parent::tearDown();
    }

    private function writeCsv(string $content): string
    {
        $path = tempnam(sys_get_temp_dir(), 'mjy-geoip');
        file_put_contents($path, $content);
        $this->scratch[] = $path;
        return $path;
    }

    /** 一份形状与 DB-IP Lite 导出一致的小样本：引号、IPv4、IPv6 都有。 */
    private function sampleCsv(): string
    {
        return $this->writeCsv(
            "1.0.1.0,1.0.3.255,CN\n"
            . "\"8.8.8.0\",\"8.8.8.255\",\"US\"\n"
            . "2001:db8::,2001:db8::ffff,CN-BJ\n"
        );
    }

    public function testTheIndexAnswersTheSameAsALinearScanDid()
    {
        $resolver = new \MjyCsvRegionResolver($this->sampleCsv());

        $this->assertSame('CN', $resolver->resolve('1.0.2.9'));
        $this->assertSame('US', $resolver->resolve('8.8.8.8'));
        $this->assertSame('CN-BJ', $resolver->resolve('2001:db8::1'));
        $this->assertNull($resolver->resolve('9.9.9.9'));
        $this->assertNull($resolver->resolve('bogus'));
    }

    public function testRangeBoundariesAreInclusive()
    {
        $resolver = new \MjyCsvRegionResolver($this->sampleCsv());

        $this->assertSame('CN', $resolver->resolve('1.0.1.0'), '起点在范围内');
        $this->assertSame('CN', $resolver->resolve('1.0.3.255'), '终点在范围内');
        $this->assertNull($resolver->resolve('1.0.0.255'), '起点前一个不在');
        $this->assertNull($resolver->resolve('1.0.4.0'), '终点后一个不在');
    }

    /** 这一条才是重点：查询走的是索引，不是 CSV。 */
    public function testLookupsNoLongerReadTheCsv()
    {
        $csv = $this->sampleCsv();
        $resolver = new \MjyCsvRegionResolver($csv);
        $this->assertSame('CN', $resolver->resolve('1.0.2.9'), '第一次查询会顺带把索引建出来');

        unlink($csv);

        $fresh = new \MjyCsvRegionResolver($csv);
        $this->assertSame('CN', $fresh->resolve('1.0.2.9'), 'CSV 没了也应当查得到——答案在索引里');
        $this->assertSame('US', $fresh->resolve('8.8.8.8'));
    }

    public function testAChangedCsvRebuildsTheIndex()
    {
        $csv = $this->sampleCsv();
        $resolver = new \MjyCsvRegionResolver($csv);
        $this->assertSame('CN', $resolver->resolve('1.0.2.9'));

        // 改内容并把修改时间推到未来，免得同一秒内的改写看起来「不比索引新」。
        file_put_contents($csv, "1.0.1.0,1.0.3.255,JP\n");
        touch($csv, time() + 10);

        $this->assertSame('JP', (new \MjyCsvRegionResolver($csv))->resolve('1.0.2.9'));
    }

    /** 几千条范围里挑中正确的一条——逐行扫描会对，二分写错边界就会错。 */
    public function testABigSortedFileIsSearchedCorrectlyAtEveryRange()
    {
        $lines = [];
        for ($block = 1; $block <= 200; $block++) {
            $lines[] = sprintf('10.%d.0.0,10.%d.255.255,R%d', $block, $block, $block);
        }
        $resolver = new \MjyCsvRegionResolver($this->writeCsv(implode("\n", $lines) . "\n"));

        for ($block = 1; $block <= 200; $block++) {
            $this->assertSame('R' . $block, $resolver->resolve('10.' . $block . '.7.7'));
        }
        $this->assertNull($resolver->resolve('10.201.0.0'));
        $this->assertNull($resolver->resolve('9.255.255.255'));
    }

    /** CSV 未按起始地址排序时，索引自己排——不能靠数据源的好意。 */
    public function testAnUnsortedCsvIsStillSearchedCorrectly()
    {
        $resolver = new \MjyCsvRegionResolver($this->writeCsv(
            "203.0.113.0,203.0.113.255,SG\n"
            . "1.0.1.0,1.0.3.255,CN\n"
            . "100.64.0.0,100.127.255.255,US\n"
        ));

        $this->assertSame('CN', $resolver->resolve('1.0.2.9'));
        $this->assertSame('US', $resolver->resolve('100.100.1.1'));
        $this->assertSame('SG', $resolver->resolve('203.0.113.9'));
        $this->assertNull($resolver->resolve('198.51.100.1'));
    }

    public function testMalformedRowsAreSkippedRatherThanPoisoningTheIndex()
    {
        $resolver = new \MjyCsvRegionResolver($this->writeCsv(
            "not-an-ip,1.0.3.255,XX\n"
            . "1.0.1.0,also-not-an-ip,XX\n"
            . "1.0.1.0,1.0.3.255\n"
            . "1.0.1.0,1.0.3.255,\n"
            . "8.8.8.0,8.8.8.255,US\n"
        ));

        $this->assertSame('US', $resolver->resolve('8.8.8.8'));
        $this->assertNull($resolver->resolve('1.0.2.9'), '坏行不该变成一条可命中的范围');
    }

    /** IPv4 与 IPv6 存在同一份索引的两段里，互不串味。 */
    public function testTheTwoAddressFamiliesDoNotLeakIntoEachOther()
    {
        $resolver = new \MjyCsvRegionResolver($this->writeCsv(
            "0.0.0.0,255.255.255.255,US\n"
            . "2001:db8::,2001:db8::ffff,CN-BJ\n"
        ));

        $this->assertSame('US', $resolver->resolve('8.8.8.8'));
        $this->assertSame('CN-BJ', $resolver->resolve('2001:db8::1'));
        $this->assertNull($resolver->resolve('2001:db9::1'));
    }

    public function testMissingDataSourceResolvesNothing()
    {
        $this->assertNull((new \MjyCsvRegionResolver('/nonexistent/geoip.csv'))->resolve('8.8.8.8'));
        $this->assertNull((new \MjyNullRegionResolver())->resolve('8.8.8.8'));
    }

    /** 索引写不出来时不能整个哑掉：退回逐行扫描，答案照旧，只是慢。 */
    public function testAnUnwritableIndexFallsBackToScanningRatherThanFailing()
    {
        $csv = $this->sampleCsv();
        $resolver = new \MjyCsvRegionResolver($csv, '/nonexistent-dir/geoip.mjyidx');

        $this->assertSame('CN', $resolver->resolve('1.0.2.9'));
        $this->assertNull($resolver->resolve('9.9.9.9'));
    }

    public function testATruncatedIndexIsRebuiltInsteadOfReturningGarbage()
    {
        $csv = $this->sampleCsv();
        $this->assertSame('CN', (new \MjyCsvRegionResolver($csv))->resolve('1.0.2.9'));

        $index = \MjyGeoIpIndex::pathFor($csv);
        file_put_contents($index, substr((string) file_get_contents($index), 0, 12));
        touch($index, time() - 60);

        $this->assertSame('CN', (new \MjyCsvRegionResolver($csv))->resolve('1.0.2.9'));
    }
}
