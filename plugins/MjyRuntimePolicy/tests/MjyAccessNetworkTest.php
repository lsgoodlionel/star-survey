<?php

namespace ls\tests;

/**
 * WP-04 访问密码与网络规则：PBKDF2 校验、CIDR 匹配、可信代理下的客户端 IP、离线地区数据源。
 *
 * 引擎的 getIPAddress()（common_helper.php:5287）无条件信任 Client-IP / X-Forwarded-For，
 * 任何人都能伪造——插件必须自己取 IP。
 */
class MjyAccessNetworkTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    /** 网关 pubgw.policy.password.hash_password('s3cret', 100000, b'0123456789abcdef') 的输出。 */
    private const S3CRET_HASH = 'pbkdf2-sha256$100000$MDEyMzQ1Njc4OWFiY2RlZg==$3FoEFG2OYy4tuwfB20lqThl5uly72sspE2cf0L6qdp4=';
    /** 同上，密码为中文"口令"，盐 b'fedcba9876543210'。 */
    private const CHINESE_HASH = 'pbkdf2-sha256$100000$ZmVkY2JhOTg3NjU0MzIxMA==$avn91V4t9FgfxfM/FFB/lQSbW1RhyLGmBdV7faMACIo=';

    /** @var string|null */
    private $csvPath;

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

    public function tearDown(): void
    {
        if ($this->csvPath !== null) {
            @unlink($this->csvPath);
        }
        parent::tearDown();
    }

    public function testPasswordHashFromThePlatformVerifies()
    {
        $this->assertTrue(\MjyPasswordHash::verify('s3cret', self::S3CRET_HASH));
        $this->assertTrue(\MjyPasswordHash::verify('口令', self::CHINESE_HASH));
    }

    public function testWrongPasswordOrMalformedHashFails()
    {
        $this->assertFalse(\MjyPasswordHash::verify('S3cret', self::S3CRET_HASH));
        $this->assertFalse(\MjyPasswordHash::verify('', self::S3CRET_HASH));
        $this->assertFalse(\MjyPasswordHash::verify('s3cret', 's3cret'));
        $this->assertFalse(\MjyPasswordHash::verify('s3cret', 'pbkdf2-sha256$1000$MDEyMzQ1Njc4OWFiY2RlZg==$AAAA'));
    }

    public function testCidrMatchingCoversBothFamilies()
    {
        $this->assertTrue(\MjyIpRules::matches('10.1.2.3', '10.0.0.0/8'));
        $this->assertFalse(\MjyIpRules::matches('11.1.2.3', '10.0.0.0/8'));
        $this->assertTrue(\MjyIpRules::matches('10.9.9.9', '10.9.9.9/32'));
        $this->assertTrue(\MjyIpRules::matches('2001:db8::1', '2001:db8::/32'));
        $this->assertFalse(\MjyIpRules::matches('2001:db9::1', '2001:db8::/32'));
        $this->assertTrue(\MjyIpRules::matches('192.168.1.200', '192.168.1.128/25'));
        $this->assertFalse(\MjyIpRules::matches('192.168.1.100', '192.168.1.128/25'));
        $this->assertTrue(\MjyIpRules::matches('8.8.8.8', '0.0.0.0/0'));
    }

    public function testAddressFamiliesNeverMatchEachOther()
    {
        $this->assertFalse(\MjyIpRules::matches('::ffff:10.1.2.3', '10.0.0.0/8'));
        $this->assertFalse(\MjyIpRules::matches('10.1.2.3', '::/0'));
        $this->assertFalse(\MjyIpRules::matches('not-an-ip', '10.0.0.0/8'));
    }

    public function testForwardedHeadersAreIgnoredWithoutATrustedProxy()
    {
        $server = [
            'REMOTE_ADDR' => '203.0.113.7',
            'HTTP_X_FORWARDED_FOR' => '10.1.1.1',
            'HTTP_CLIENT_IP' => '10.2.2.2',
        ];

        $this->assertSame('203.0.113.7', \MjyIpRules::clientIp($server, []));
        $this->assertSame('203.0.113.7', \MjyIpRules::clientIp($server, ['192.168.0.0/16']));
    }

    public function testTrustedProxyYieldsTheRightmostUntrustedHop()
    {
        $server = [
            'REMOTE_ADDR' => '192.168.0.10',
            // 最左边是客户端自己写的（可伪造），右边是各级代理追加的。
            'HTTP_X_FORWARDED_FOR' => '10.1.1.1, 198.51.100.9, 192.168.0.11',
        ];

        $this->assertSame('198.51.100.9', \MjyIpRules::clientIp($server, ['192.168.0.0/16']));
    }

    public function testGarbageInForwardedChainStopsAtTheProxy()
    {
        $server = ['REMOTE_ADDR' => '192.168.0.10', 'HTTP_X_FORWARDED_FOR' => 'evil, <script>'];

        $this->assertSame('192.168.0.10', \MjyIpRules::clientIp($server, ['192.168.0.0/16']));
    }

    public function testCsvRegionResolverFindsRanges()
    {
        $resolver = new \MjyCsvRegionResolver($this->writeCsv(
            "1.0.1.0,1.0.3.255,CN\n"
            . "\"8.8.8.0\",\"8.8.8.255\",\"US\"\n"
            . "2001:db8::,2001:db8:ffff:ffff:ffff:ffff:ffff:ffff,CN-BJ\n"
        ));

        $this->assertSame('CN', $resolver->resolve('1.0.2.9'));
        $this->assertSame('US', $resolver->resolve('8.8.8.8'));
        $this->assertSame('CN-BJ', $resolver->resolve('2001:db8::5'));
        $this->assertNull($resolver->resolve('9.9.9.9'));
        $this->assertNull($resolver->resolve('bogus'));
    }

    public function testMissingDataSourceResolvesNothing()
    {
        $this->assertNull((new \MjyCsvRegionResolver('/nonexistent/geoip.csv'))->resolve('1.0.2.9'));
        $this->assertNull((new \MjyNullRegionResolver())->resolve('1.0.2.9'));
    }

    public function testRegionRuleForACountryCoversItsSubdivisions()
    {
        $this->assertTrue(\MjyIpRules::regionMatches('CN-BJ', 'CN'));
        $this->assertTrue(\MjyIpRules::regionMatches('CN', 'CN'));
        $this->assertFalse(\MjyIpRules::regionMatches('CN', 'CN-BJ'));
        $this->assertFalse(\MjyIpRules::regionMatches('CNX', 'CN'));
    }

    private function writeCsv(string $content): string
    {
        $this->csvPath = tempnam(sys_get_temp_dir(), 'mjy-geoip');
        file_put_contents($this->csvPath, $content);
        return $this->csvPath;
    }
}
