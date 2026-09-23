<?php

namespace ls\tests;

/**
 * WP-06.4：网关↔插件鉴权通道的验签（ADR 0018，契约 plugin-channel-v1）。
 *
 * 固定向量与网关侧 tests/test_channel.py 用的是同一组值：任一端改了派生或签名算法，
 * 两端都会红。向量由契约算出（HMAC-SHA256），不是从实现里抄的。
 *
 * 本类只测「验签之前」那一段——它是匿名可达面，所以每一条拒绝都必须归到同一个
 * 出口（统一 401），原因只作为日志用的稳定码返回。
 */
class MjyChannelAuthTest extends TestBaseClass
{
    private const INSTANCE = 'hd-engine-01';
    private const INSTANCE_SECRET = '0123456789abcdef0123456789abcdef-instance';
    private const CHANNEL_SECRET = '204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31';
    private const OTHER_SECRET = 'fedcba9876543210fedcba9876543210-previous';
    private const NOW = 1800000000;
    private const CANONICAL = 'function=extensionAnswers&generation=gen-1&plugin=MjyQuestionExtensions'
        . '&questionCodes=TABLE1&responseIds=1001%2C1002&sid=42&ts=1800000000';
    private const SIGNATURE = '5c9b53726ed822c5ce91ea2586453ce85e1698b043e421a20527d5e0e82be7f4';

    /** 契约里那张 URL 的参数（不含 sig）。 */
    private function params(array $overrides = []): array
    {
        return $overrides + [
            'plugin' => 'MjyQuestionExtensions',
            'function' => 'extensionAnswers',
            'sid' => '42',
            'generation' => 'gen-1',
            'responseIds' => '1001,1002',
            'questionCodes' => 'TABLE1',
            'ts' => (string) self::NOW,
        ];
    }

    private function auth(array $instanceSecrets = [self::INSTANCE_SECRET]): \MjyChannelAuth
    {
        return new \MjyChannelAuth($instanceSecrets, self::INSTANCE);
    }

    /** 用给定密钥为一组参数签名，返回含 sig 的完整参数。 */
    private function signed(array $params, string $channelSecret = self::CHANNEL_SECRET): array
    {
        $canonical = \MjyChannelAuth::canonicalQuery($params);
        $params['sig'] = hash_hmac('sha256', $params['ts'] . '.' . $canonical, $channelSecret);
        return $params;
    }

    // ---- 密钥派生 ----

    public function testDerivesTheContractVector(): void
    {
        $this->assertSame(
            self::CHANNEL_SECRET,
            \MjyChannelAuth::deriveChannelSecret(self::INSTANCE_SECRET, self::INSTANCE)
        );
    }

    public function testEachInstanceGetsItsOwnChannelSecret(): void
    {
        $this->assertNotSame(
            self::CHANNEL_SECRET,
            \MjyChannelAuth::deriveChannelSecret(self::INSTANCE_SECRET, 'hd-engine-02')
        );
    }

    /** 单向：通道密钥泄露推不出实例密钥，网关被攻破也伪造不了引擎事件。 */
    public function testChannelSecretDoesNotContainTheInstanceSecret(): void
    {
        $derived = \MjyChannelAuth::deriveChannelSecret(self::INSTANCE_SECRET, self::INSTANCE);
        $this->assertStringNotContainsString(self::INSTANCE_SECRET, $derived);
    }

    // ---- 规范化查询串（与 Python 侧必须逐字一致）----

    public function testCanonicalQueryMatchesTheContractVector(): void
    {
        $this->assertSame(self::CANONICAL, \MjyChannelAuth::canonicalQuery($this->params()));
    }

    public function testCanonicalQuerySortsByParameterName(): void
    {
        $this->assertSame('a=1&b=2&c=3', \MjyChannelAuth::canonicalQuery(['c' => '3', 'a' => '1', 'b' => '2']));
    }

    public function testCanonicalQueryPercentEncodesReservedCharacters(): void
    {
        $this->assertSame('ids=1%2C2', \MjyChannelAuth::canonicalQuery(['ids' => '1,2']));
    }

    public function testCanonicalQueryLeavesUnreservedCharactersAlone(): void
    {
        $this->assertSame('v=A-z0.9_x~y', \MjyChannelAuth::canonicalQuery(['v' => 'A-z0.9_x~y']));
    }

    public function testCanonicalQueryEncodesNonAsciiAsUtf8(): void
    {
        $this->assertSame('v=%E7%94%B2', \MjyChannelAuth::canonicalQuery(['v' => '甲']));
    }

    public function testCanonicalQueryIgnoresTheSignatureParameter(): void
    {
        $this->assertSame(
            \MjyChannelAuth::canonicalQuery($this->params()),
            \MjyChannelAuth::canonicalQuery($this->params() + ['sig' => self::SIGNATURE])
        );
    }

    // ---- 验签 ----

    public function testAcceptsTheContractVector(): void
    {
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertSame('', $this->auth()->check($params, self::NOW));
    }

    public function testAcceptsASignatureInUppercaseHex(): void
    {
        $params = $this->params();
        $params['sig'] = strtoupper(self::SIGNATURE);
        $this->assertSame('', $this->auth()->check($params, self::NOW));
    }

    public function testRejectsAMissingSignature(): void
    {
        $this->assertNotSame('', $this->auth()->check($this->params(), self::NOW));
    }

    public function testRejectsASignatureThatIsNotHex(): void
    {
        $params = $this->params();
        $params['sig'] = str_repeat('z', 64);
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    public function testRejectsASignatureOfTheWrongLength(): void
    {
        $params = $this->params();
        $params['sig'] = substr(self::SIGNATURE, 0, 63);
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    public function testRejectsASignatureMadeWithAnotherSecret(): void
    {
        $params = $this->signed($this->params(), str_repeat('a', 64));
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    /**
     * 签名覆盖整个查询串：截获一个合法请求的人改不成「读别人的答卷」。
     * 这是本通道最要紧的一条性质。
     */
    public function testRejectsARequestRetargetedToAnotherSurvey(): void
    {
        $params = $this->signed($this->params());
        $params['sid'] = '43';
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    public function testRejectsARequestRetargetedToAnotherResponse(): void
    {
        $params = $this->signed($this->params());
        $params['responseIds'] = '2001,2002';
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    public function testRejectsARequestRetargetedToAnotherPluginFunction(): void
    {
        $params = $this->signed($this->params());
        $params['function'] = 'somethingElse';
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    /** 追加一个不在签名里的参数同样签不过。 */
    public function testRejectsAnAppendedParameter(): void
    {
        $params = $this->signed($this->params());
        $params['debug'] = '1';
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    // ---- 时间戳窗口 ----

    public function testAcceptsATimestampAtTheEdgeOfTheWindow(): void
    {
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertSame('', $this->auth()->check($params, self::NOW + 300));
        $this->assertSame('', $this->auth()->check($params, self::NOW - 300));
    }

    public function testRejectsAStaleTimestamp(): void
    {
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertNotSame('', $this->auth()->check($params, self::NOW + 301));
    }

    public function testRejectsATimestampTooFarInTheFuture(): void
    {
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertNotSame('', $this->auth()->check($params, self::NOW - 301));
    }

    public function testRejectsAMissingTimestamp(): void
    {
        $params = $this->params();
        unset($params['ts']);
        $params['sig'] = self::SIGNATURE;
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    public function testRejectsANonNumericTimestamp(): void
    {
        $params = $this->signed($this->params(['ts' => 'soon']));
        $this->assertNotSame('', $this->auth()->check($params, self::NOW));
    }

    // ---- 密钥轮换（ADR 0018 决定 3）----

    public function testAcceptsASignatureMadeWithThePreviousSecret(): void
    {
        $auth = $this->auth([self::OTHER_SECRET, self::INSTANCE_SECRET]);
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertSame('', $auth->check($params, self::NOW));
    }

    public function testAcceptsASignatureMadeWithTheCurrentSecretDuringRotation(): void
    {
        $auth = $this->auth([self::INSTANCE_SECRET, self::OTHER_SECRET]);
        $current = \MjyChannelAuth::deriveChannelSecret(self::OTHER_SECRET, self::INSTANCE);
        $this->assertSame('', $auth->check($this->signed($this->params(), $current), self::NOW));
    }

    // ---- 失败即关闭 ----

    public function testRejectsEverythingWhenNoSecretIsConfigured(): void
    {
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertNotSame('', $this->auth([])->check($params, self::NOW));
    }

    public function testIgnoresAnInstanceSecretThatIsTooShort(): void
    {
        // 不足 32 字节的密钥不派生，等于没配；不得因为「配了点什么」就放行。
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertNotSame('', $this->auth(['short'])->check($params, self::NOW));
    }

    public function testIgnoresAnEmptyInstanceSecret(): void
    {
        $params = $this->params();
        $params['sig'] = self::SIGNATURE;
        $this->assertNotSame('', $this->auth(['', self::OTHER_SECRET])->check($params, self::NOW));
    }

    /** 拒绝原因是给日志用的稳定码，绝不能把密钥或签名带出去。 */
    public function testReasonCodeCarriesNoSecretAndNoSignature(): void
    {
        $params = $this->signed($this->params(), str_repeat('a', 64));
        $reason = $this->auth()->check($params, self::NOW);
        $this->assertNotSame('', $reason);
        $this->assertStringNotContainsString(self::CHANNEL_SECRET, $reason);
        $this->assertStringNotContainsString(self::INSTANCE_SECRET, $reason);
        $this->assertStringNotContainsString($params['sig'], $reason);
    }
}
