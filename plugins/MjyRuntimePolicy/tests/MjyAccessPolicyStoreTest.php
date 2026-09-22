<?php

namespace ls\tests;

/**
 * WP-04 策略下发：网关把策略编进 LSS 的 plugin_settings，引擎导入时写进
 * lime_plugin_settings（model=Survey, model_id=新 sid）。插件从那里读，
 * 并通过 policyStatus 让网关回读核对。
 *
 * 读不出、多于一份、解析不了都必须让判定失败（fail closed）。
 */
class MjyAccessPolicyStoreTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';

    /** @var int */
    private static $pluginId;

    /** @var \MjyAccessPolicyStore */
    private $store;

    /** @var int */
    private $policySurveyId;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        $record = self::installAndActivatePlugin(self::PLUGIN_NAME);
        \App()->getPluginManager()->loadPlugin(self::PLUGIN_NAME, $record->id);
        self::$pluginId = (int) $record->id;
    }

    public static function tearDownAfterClass(): void
    {
        self::deActivatePlugin(self::PLUGIN_NAME);
        parent::tearDownAfterClass();
    }

    public function setUp(): void
    {
        parent::setUp();
        $this->store = new \MjyAccessPolicyStore(\App()->getDb(), self::$pluginId);
        $this->policySurveyId = random_int(600000, 699999);
    }

    public function tearDown(): void
    {
        \PluginSetting::model()->deleteAllByAttributes(['model' => 'Survey', 'model_id' => $this->policySurveyId]);
        parent::tearDown();
    }

    public function testSurveyWithoutPolicyHasNone()
    {
        $this->assertNull($this->store->find($this->policySurveyId));
        $this->assertSame(
            ['surveyId' => $this->policySurveyId, 'rows' => 0, 'valid' => false, 'policyDigest' => null],
            $this->store->status($this->policySurveyId)
        );
    }

    public function testImportedPolicyIsReadAndReportedByDigest()
    {
        // Arrange：导入端写的就是 LSS 里的原文。
        $raw = json_encode(MjyAccessPolicyTest::payload());
        $this->insertSetting($this->policySurveyId, $raw);

        // Act
        $policy = $this->store->find($this->policySurveyId);
        $status = $this->store->status($this->policySurveyId);

        // Assert
        $this->assertSame(hash('sha256', $raw), $policy->digest());
        $this->assertSame(
            ['surveyId' => $this->policySurveyId, 'rows' => 1, 'valid' => true, 'policyDigest' => hash('sha256', $raw)],
            $status
        );
    }

    public function testTwoCopiesAreAmbiguousAndFailClosed()
    {
        $raw = json_encode(MjyAccessPolicyTest::payload());
        $this->insertSetting($this->policySurveyId, $raw);
        $this->insertSetting($this->policySurveyId, $raw);

        $this->assertSame(2, $this->store->status($this->policySurveyId)['rows']);
        $this->expectException(\RuntimeException::class);
        $this->store->find($this->policySurveyId);
    }

    public function testUnparseablePolicyFailsClosed()
    {
        $this->insertSetting($this->policySurveyId, '{"schema":"mjy-access-policy/9"}');

        $this->assertFalse($this->store->status($this->policySurveyId)['valid']);
        $this->expectException(\InvalidArgumentException::class);
        $this->store->find($this->policySurveyId);
    }

    public function testRowsOfOtherSurveysAndPluginsAreIgnored()
    {
        $this->insertSetting($this->policySurveyId + 1, json_encode(MjyAccessPolicyTest::payload()));
        $this->insertSetting($this->policySurveyId, json_encode(MjyAccessPolicyTest::payload()), self::$pluginId + 1000);

        $this->assertNull($this->store->find($this->policySurveyId));
        \PluginSetting::model()->deleteAllByAttributes(['model' => 'Survey', 'model_id' => $this->policySurveyId + 1]);
    }

    private function insertSetting(int $surveyId, string $value, ?int $pluginId = null): void
    {
        \App()->getDb()->createCommand()->insert('{{plugin_settings}}', [
            'plugin_id' => $pluginId ?? self::$pluginId,
            'model' => 'Survey',
            'model_id' => $surveyId,
            'key' => \MjyAccessPolicyStore::POLICY_KEY,
            'value' => $value,
        ]);
    }
}
