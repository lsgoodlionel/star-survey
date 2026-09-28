<?php

namespace ls\tests;

/**
 * 答案键的读取与回读状态（WP-09.1，契约 survey-exam-v1 §4、§5）。
 *
 * 读路径与访问策略同一条载体（plugin_settings，model='Survey'），失败语义也一样：
 * 恰好一行才算数。判分场景下"有两份答案键"必须是错误而不是"挑一份用"。
 *
 * 另有一条专门的断言：**状态端点不回答案内容**。这个端点走 newDirectRequest，
 * 是公开路由，没有鉴权；它要是把答案回出去，等于换个地方下发。
 */
class MjyExamKeyStoreTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const SENTINEL = 'MJYSENTINELSTORE4T8';

    /** @var int */
    private $pluginId;

    /** @var int TestBaseClass 自己有个静态 $surveyId，不能同名 */
    private $examSurveyId;

    /** @var \MjyExamKeyStore */
    private $store;

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

    protected function setUp(): void
    {
        parent::setUp();
        $this->pluginId = (int) \Plugin::model()->findByAttributes(['name' => self::PLUGIN_NAME])->id;
        // 随机 sid：测试之间不互相干扰，也不依赖清库。
        $this->examSurveyId = random_int(940000, 949000);
        $this->store = new \MjyExamKeyStore(\App()->getDb(), $this->pluginId);
    }

    protected function tearDown(): void
    {
        \App()->getDb()->createCommand()->delete(
            '{{plugin_settings}}',
            'plugin_id = :plugin AND model_id = :sid',
            [':plugin' => $this->pluginId, ':sid' => $this->examSurveyId]
        );
        parent::tearDown();
    }

    private function payload(string $answer = self::SENTINEL): string
    {
        return json_encode([
            'schema' => 'mjy-exam-key/1',
            'answerKey' => [[
                'question' => 'QTEXT', 'kind' => 'text', 'correct' => [$answer], 'points' => 3.0,
                'match' => ['ignoreCase' => false, 'trim' => true],
            ]],
        ]);
    }

    /**
     * $key 缺省用字面量而不是 \MjyExamKeyStore::EXAM_KEY：缺省值里的类常量会在
     * 声明期解析，那时 loadPlugin() 还没把插件目录加进引擎的类导入路径，
     * 整个测试文件会加载失败（而且是静默退出，不报错）。
     */
    private function insertSetting(string $value, string $key = 'mjy_exam_key'): void
    {
        \App()->getDb()->createCommand()->insert('{{plugin_settings}}', [
            'plugin_id' => $this->pluginId,
            'model' => 'Survey',
            'model_id' => $this->examSurveyId,
            'key' => $key,
            'value' => $value,
        ]);
    }

    /** 上面那个字面量缺省值不能与常量漂移。 */
    public function testTheSettingKeyIsTheOneTheGatewayWrites(): void
    {
        $this->assertSame('mjy_exam_key', \MjyExamKeyStore::EXAM_KEY);
    }

    public function testSurveyWithoutAnAnswerKeyHasNone(): void
    {
        $this->assertNull($this->store->find($this->examSurveyId));
    }

    public function testImportedAnswerKeyIsRead(): void
    {
        $this->insertSetting($this->payload());

        $key = $this->store->find($this->examSurveyId);

        $this->assertSame(['QTEXT'], $key->questionCodes());
        $this->assertSame([self::SENTINEL], $key->find('QTEXT')->correct());
    }

    public function testTwoCopiesAreAmbiguousAndFailClosed(): void
    {
        $this->insertSetting($this->payload());
        $this->insertSetting($this->payload('别的答案'));

        $this->expectException(\RuntimeException::class);
        $this->store->find($this->examSurveyId);
    }

    public function testUnparseableAnswerKeyFailsClosed(): void
    {
        $this->insertSetting('{"schema":"mjy-exam-key/1","answerKey":[{"question":""}]}');

        $this->expectException(\InvalidArgumentException::class);
        $this->store->find($this->examSurveyId);
    }

    public function testRowsOfOtherKeysAreIgnored(): void
    {
        $this->insertSetting($this->payload(), \MjyAccessPolicyStore::POLICY_KEY);  // 方法体里引用是安全的

        $this->assertNull($this->store->find($this->examSurveyId));
    }

    public function testStatusReportsTheDigestAndQuestionCount(): void
    {
        $text = $this->payload();
        $this->insertSetting($text);

        $status = $this->store->status($this->examSurveyId);

        $this->assertSame(1, $status['rows']);
        $this->assertTrue($status['valid']);
        $this->assertSame(hash('sha256', $text), $status['examDigest']);
        $this->assertSame(1, $status['questions']);
    }

    public function testStatusOfAnUnparseableKeyIsNotValid(): void
    {
        $this->insertSetting('{"schema":"mjy-exam-key/9"}');

        $status = $this->store->status($this->examSurveyId);

        $this->assertFalse($status['valid']);
        $this->assertNull($status['questions']);
    }

    /**
     * 回读端点没有鉴权。答案内容绝不能出现在它的应答里——这是"答案不下发"
     * 在**服务端接口面**上的那一半（页面那一半由端到端 exam_key.py 钉住）。
     */
    public function testStatusNeverLeaksTheAnswer(): void
    {
        $this->insertSetting($this->payload());

        $encoded = json_encode($this->store->status($this->examSurveyId), JSON_UNESCAPED_UNICODE);

        $this->assertStringNotContainsString(self::SENTINEL, $encoded);
        $this->assertStringNotContainsString('QTEXT', $encoded);
        $this->assertStringNotContainsString('correct', $encoded);
    }
}
