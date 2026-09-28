<?php

namespace ls\tests;

/**
 * RemoteControl 写答卷闸门的判定（ADR 0021；P0 发现 12、21）。
 *
 * {@see \MjyRpcGate} 刻意没有任何引擎依赖：判定完全由入参决定，不碰 Yii、不碰数据库。
 * 这里仍然走 TestBaseClass，只为了让插件类被自动加载、并与本插件其余用例同在一个套件里。
 * 请求生命周期那半边（读 php://input、写 403、把动作关掉）由端到端脚本
 * platform/tests/e2e/rpc_gate.py 在真引擎上验证。
 */
class MjyRpcGateTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const SESSION = 'a-real-session-key';

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

    private function jsonRpc(string $method, array $params = []): string
    {
        return (string) json_encode(['method' => $method, 'params' => $params, 'id' => 1]);
    }

    private function gate(bool $override = false): \MjyRpcGate
    {
        return new \MjyRpcGate($override);
    }

    // ------------------------------------------------------------ 端点识别

    public function testRecognisesTheRemoteControlEndpoint(): void
    {
        $this->assertTrue($this->gate()->isRemoteControl('admin', 'remotecontrol'));
        $this->assertTrue($this->gate()->isRemoteControl('Admin', 'RemoteControl'));
    }

    public function testLeavesEveryOtherRouteAlone(): void
    {
        $gate = $this->gate();

        $this->assertFalse($gate->isRemoteControl('survey', 'index'));
        $this->assertFalse($gate->isRemoteControl('admin', 'responses'));
        $this->assertFalse($gate->isRemoteControl('plugins', 'direct'));
        $this->assertFalse($gate->isRemoteControl(null, null));
    }

    // ------------------------------------------------------------ 方法名解析

    public function testReadsTheJsonRpcMethodName(): void
    {
        $this->assertSame('add_response', $this->gate()->methodOf($this->jsonRpc('add_response')));
    }

    public function testReadsTheXmlRpcMethodName(): void
    {
        $body = '<?xml version="1.0"?><methodCall><methodName>update_response</methodName>'
            . '<params><param><value><string>k</string></value></param></params></methodCall>';

        $this->assertSame('update_response', $this->gate()->methodOf($body));
    }

    public function testReportsAnUnreadableBodyAsUnknown(): void
    {
        $this->assertNull($this->gate()->methodOf('not json at all'));
        $this->assertNull($this->gate()->methodOf('{"params":[]}'));
        $this->assertNull($this->gate()->methodOf(''));
    }

    // ------------------------------------------------------------ 拒绝

    /** @dataProvider blockedMethods */
    public function testRefusesTheTwoMethodsThatBypassTheGate(string $method): void
    {
        $body = $this->jsonRpc($method, [self::SESSION, 511001, ['QNOTE' => 'smuggled']]);

        $this->assertTrue($this->gate()->isBlocked($body));
    }

    public static function blockedMethods(): array
    {
        return [['add_response'], ['update_response']];
    }

    public function testRefusesRegardlessOfCaseAndPadding(): void
    {
        $this->assertTrue($this->gate()->isBlocked($this->jsonRpc(' Add_Response ')));
    }

    public function testRefusesTheXmlRpcVariant(): void
    {
        $body = '<methodCall><methodName>add_response</methodName></methodCall>';

        $this->assertTrue($this->gate()->isBlocked($body));
    }

    /**
     * XML-RPC 里只看第一个 methodName 就能被绕过：前面塞一个无害的名字，
     * 真正的 methodCall 排在后面。所以**每一个** methodName 都要看。
     */
    public function testRefusesABlockedMethodNameAnywhereInTheXmlBody(): void
    {
        $body = '<decoy><methodName>get_fieldmap</methodName></decoy>'
            . '<methodCall><methodName>add_response</methodName></methodCall>';

        $this->assertTrue($this->gate()->isBlocked($body));
    }

    /**
     * 注释版诱饵（独立安全审查提出的构造）：注释在 XML 里是良构的，
     * 引擎的 DOMDocument 会跳过它读到后面真正的 methodCall；只看第一个
     * `<methodName>` 的闸门却会被注释里的无害名字骗过去。
     */
    public function testRefusesADecoyMethodNameHiddenInAnXmlComment(): void
    {
        $body = '<!--<methodName>get_fieldmap</methodName>-->'
            . '<methodCall><methodName>add_response</methodName><params/></methodCall>';

        $this->assertTrue($this->gate()->isBlocked($body));
    }

    /**
     * JSON 里重复键的取舍由 json_decode 决定，而引擎用的是同一个函数——
     * 后一个 method 生效，闸门与引擎看到的是同一个名字。
     */
    public function testFollowsPhpsOwnRuleForADuplicatedMethodKey(): void
    {
        $this->assertTrue($this->gate()->isBlocked('{"method":"get_fieldmap","method":"add_response","id":1}'));
        $this->assertFalse($this->gate()->isBlocked('{"method":"add_response","method":"get_fieldmap","id":1}'));
    }

    /**
     * 解析不出结构、但正文里有被禁方法名 → 按拒处理。
     * 我们的解析器与引擎的解析器不必完全一致，读不懂的正文不能当成安全。
     */
    public function testRefusesAnUnparsableBodyThatMentionsABlockedMethod(): void
    {
        $this->assertTrue($this->gate()->isBlocked('<<garbage>> add_response <<garbage>>'));
    }

    public function testLetsAnUnparsableBodyWithoutBlockedNamesThrough(): void
    {
        $this->assertFalse($this->gate()->isBlocked('total garbage'));
    }

    // ------------------------------------------------------------ 放行

    /**
     * 发布网关用到的每一个方法都必须照旧放行——闸门拦错了就等于把发布链路关掉。
     *
     * @dataProvider gatewayMethods
     */
    public function testLetsEveryMethodThePublishGatewayUsesThrough(string $method): void
    {
        $body = $this->jsonRpc($method, [self::SESSION, 511001]);

        $this->assertFalse($this->gate()->isBlocked($body), $method . ' 被误拦了');
    }

    public static function gatewayMethods(): array
    {
        return array_map(static fn(string $method): array => [$method], [
            'get_session_key', 'release_session_key',
            'import_survey', 'activate_survey', 'activate_tokens', 'add_participants', 'delete_survey',
            'get_fieldmap', 'list_questions', 'get_survey_properties', 'set_survey_properties',
            'export_responses',
            'get_participant_properties', 'delete_participants',
        ]);
    }

    /** 正常调用的参数里出现被禁方法名（例如问卷文案）不该被误拦。 */
    public function testDoesNotBlockOnABlockedNameInsideTheParameters(): void
    {
        $body = $this->jsonRpc('set_survey_properties', [
            self::SESSION, 511001, ['surveyls_welcometext' => 'do not use add_response'],
        ]);

        $this->assertFalse($this->gate()->isBlocked($body));
    }

    // ------------------------------------------------------------ 运维开关

    public function testTheOverrideLetsOperatorsThrough(): void
    {
        $this->assertTrue($this->gate(true)->isBlocked($this->jsonRpc('add_response')) === false);
    }

    /** @dataProvider overrideValues */
    public function testOverrideOnlyOpensForAnExplicitYes(?string $value, bool $expected): void
    {
        $this->assertSame($expected, \MjyRpcGate::fromEnvironment($value)->isOverrideEnabled());
    }

    public static function overrideValues(): array
    {
        return [
            [null, false], ['', false], ['0', false], ['false', false], ['no', false],
            ['off', false], ['2', false], [' anything ', false],
            ['1', true], ['true', true], ['TRUE', true], [' yes ', true],
        ];
    }

    // ------------------------------------------------------------ 应答与日志

    public function testTheRefusalIsAJsonRpcErrorCarryingTheRequestId(): void
    {
        $body = $this->jsonRpc('add_response');

        $decoded = json_decode($this->gate()->refusalBody($body), true);

        $this->assertSame(1, $decoded['id']);
        $this->assertNull($decoded['result']);
        $this->assertIsString($decoded['error']);
        $this->assertStringContainsString('bypasses', $decoded['error']);
    }

    public function testTheRefusalNeverEchoesTheParameters(): void
    {
        $body = $this->jsonRpc('add_response', [self::SESSION, 511001, ['QNOTE' => 'a private answer']]);

        $refusal = $this->gate()->refusalBody($body);

        $this->assertStringNotContainsString(self::SESSION, $refusal);
        $this->assertStringNotContainsString('a private answer', $refusal);
    }

    public function testTheRefusalSurvivesABodyWithoutAnId(): void
    {
        $decoded = json_decode($this->gate()->refusalBody('garbage add_response'), true);

        $this->assertNull($decoded['id']);
        $this->assertIsString($decoded['error']);
    }

    public function testTheLogLineHasTheMethodAndNoParameters(): void
    {
        $body = $this->jsonRpc('add_response', [self::SESSION]);

        $line = $this->gate()->logLine($body, 'refused');

        $this->assertStringContainsString('add_response', $line);
        $this->assertStringContainsString('refused', $line);
        $this->assertStringNotContainsString(self::SESSION, $line);
    }
}
