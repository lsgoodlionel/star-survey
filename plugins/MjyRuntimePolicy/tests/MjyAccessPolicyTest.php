<?php

namespace ls\tests;

/**
 * WP-04 访问策略载荷（网关编译，契约 survey-access-policy-v1 §4）的解析。
 *
 * 载荷来自引擎库里的插件设置，属于外部数据：任何一处形状不对都必须抛异常，
 * 由插件按"查不下去即拒绝"处理——绝不能把半个策略当成"没有限制"。
 */
class MjyAccessPolicyTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';

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

    /**
     * @return array<string, mixed>
     */
    public static function payload(array $overrides = []): array
    {
        return array_merge([
            'schema' => 'mjy-access-policy/1',
            'window' => [
                'opensAt' => '2026-10-01 01:00:00',
                'closesAt' => '2026-10-07 10:30:00',
                'timezone' => 'Asia/Shanghai',
                'opensAtLocal' => '2026-10-01T09:00',
                'closesAtLocal' => '2026-10-07T18:30',
            ],
            'passwordHash' => null,
            'responses' => [['by' => 'token', 'max' => 1], ['by' => 'ip', 'max' => 5]],
            'maxDurationSeconds' => 1800,
            'network' => [
                'allowIps' => ['10.0.0.0/8'],
                'denyIps' => ['10.9.9.9/32'],
                'allowRegions' => ['CN'],
                'denyRegions' => [],
                'regionUnknown' => 'deny',
            ],
        ], $overrides);
    }

    public function testParsesTheCanonicalPayload()
    {
        // Arrange
        $raw = json_encode(self::payload());

        // Act
        $policy = \MjyAccessPolicy::fromJson($raw);

        // Assert
        $this->assertSame('2026-10-01 01:00:00', $policy->opensAtUtc());
        $this->assertSame('2026-10-07 10:30:00', $policy->closesAtUtc());
        $this->assertSame('2026-10-07 18:30（Asia/Shanghai）', $policy->closesAtText());
        $this->assertSame([['by' => 'token', 'max' => 1], ['by' => 'ip', 'max' => 5]], $policy->responseLimits());
        $this->assertSame(1800, $policy->maxDurationSeconds());
        $this->assertSame(['10.0.0.0/8'], $policy->allowIps());
        $this->assertSame(['CN'], $policy->allowRegions());
        $this->assertTrue($policy->denyUnknownRegion());
        $this->assertSame(hash('sha256', $raw), $policy->digest());
    }

    public function testMinimalPayloadHasNoRestrictions()
    {
        $policy = \MjyAccessPolicy::fromJson(json_encode(self::payload([
            'window' => null, 'responses' => [], 'maxDurationSeconds' => null, 'network' => null,
        ])));

        $this->assertNull($policy->opensAtUtc());
        $this->assertNull($policy->passwordHash());
        $this->assertSame([], $policy->responseLimits());
        $this->assertFalse($policy->hasNetworkRules());
    }

    /**
     * @dataProvider malformedPayloads
     */
    public function testMalformedPayloadIsRejected(string $raw)
    {
        $this->expectException(\InvalidArgumentException::class);
        \MjyAccessPolicy::fromJson($raw);
    }

    public static function malformedPayloads(): array
    {
        return [
            'not json' => ['{"schema":'],
            'not an object' => ['[1,2]'],
            'unknown schema' => [json_encode(self::payload(['schema' => 'mjy-access-policy/2']))],
            'unknown identity' => [json_encode(self::payload(['responses' => [['by' => 'wechat', 'max' => 1]]]))],
            'zero limit' => [json_encode(self::payload(['responses' => [['by' => 'ip', 'max' => 0]]]))],
            'string duration' => [json_encode(self::payload(['maxDurationSeconds' => '1800']))],
            'bad utc time' => [json_encode(self::payload(['window' => ['opensAt' => 'tomorrow', 'closesAt' => null,
                'timezone' => 'UTC', 'opensAtLocal' => 'x', 'closesAtLocal' => null]]))],
            'bad cidr' => [json_encode(self::payload(['network' => ['allowIps' => ['10.0.0.0/99'], 'denyIps' => [],
                'allowRegions' => [], 'denyRegions' => [], 'regionUnknown' => 'deny']]))],
            'bad region fallback' => [json_encode(self::payload(['network' => ['allowIps' => [], 'denyIps' => [],
                'allowRegions' => ['CN'], 'denyRegions' => [], 'regionUnknown' => 'maybe']]))],
            'bad hash' => [json_encode(self::payload(['passwordHash' => 'plain-text']))],
        ];
    }
}
