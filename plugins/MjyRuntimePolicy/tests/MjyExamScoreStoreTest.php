<?php

namespace ls\tests;

/**
 * 成绩的存放（WP-10）。
 *
 * 成绩放在**插件表**里，不往引擎的答卷表加列。两个理由：答卷表的每一列都是
 * 作答者提交面的一部分（RemoteControl 的 update_response 能写它），成绩挂在那里
 * 等于给了一条"自己改分"的路；而且答卷表在停用／重新启用时会被重建。
 */
class MjyExamScoreStoreTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyRuntimePolicy';
    private const INSTANCE = 'exam-score-test';
    private const NOW = '2026-09-24 10:30:00';
    private const DIGEST = 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90';

    /** @var \MjyExamScoreStore */
    private $store;

    /** @var int */
    private $examSurveyId;

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
        $this->examSurveyId = random_int(943000, 943900);
        $this->store = new \MjyExamScoreStore(\App()->getDb(), self::INSTANCE);
        $this->store->ensureSchema();
    }

    protected function tearDown(): void
    {
        \App()->getDb()->createCommand()->delete(
            $this->store->tableName(), 'survey_id = :sid', [':sid' => $this->examSurveyId]);
        parent::tearDown();
    }

    private function result(float $score = 8.0): \MjyExamResult
    {
        return new \MjyExamResult($score, 12.0, [
            'QPICK' => ['correct' => true, 'points' => 5.0, 'answered' => true],
            'QCITY' => ['correct' => true, 'points' => 3.0, 'answered' => true],
            'QMULTI' => ['correct' => false, 'points' => 0.0, 'answered' => true],
        ]);
    }

    public function testAnUngradedResponseHasNoScore(): void
    {
        $this->assertNull($this->store->find($this->examSurveyId, 1));
    }

    public function testSavingAndReadingBackAScore(): void
    {
        $this->store->save($this->examSurveyId, 7, $this->result(), self::DIGEST, self::NOW);

        $saved = $this->store->find($this->examSurveyId, 7);

        $this->assertSame(8.0, $saved['score']);
        $this->assertSame(12.0, $saved['max_score']);
        $this->assertSame(2, $saved['correct_count']);
        $this->assertSame(3, $saved['question_count']);
        $this->assertSame(self::DIGEST, $saved['key_digest']);
        $this->assertSame(self::NOW, $saved['graded_at']);
    }

    public function testTheDetailIsStoredAsJson(): void
    {
        $this->store->save($this->examSurveyId, 7, $this->result(), self::DIGEST, self::NOW);

        $detail = json_decode($this->store->find($this->examSurveyId, 7)['detail'], true);

        $this->assertTrue($detail['QPICK']['correct']);
        $this->assertFalse($detail['QMULTI']['correct']);
    }

    /**
     * 重判要覆盖，不能留两份。一份卷子同时存在两个分数，谁都说不清哪个算数。
     */
    public function testRegradingReplacesTheEarlierScore(): void
    {
        $this->store->save($this->examSurveyId, 7, $this->result(8.0), self::DIGEST, self::NOW);

        $this->store->save($this->examSurveyId, 7, $this->result(12.0), self::DIGEST, '2026-09-24 11:00:00');

        $this->assertSame(12.0, $this->store->find($this->examSurveyId, 7)['score']);
        $this->assertSame(1, $this->store->countFor($this->examSurveyId));
    }

    public function testScoresAreScopedPerResponseAndPerSurvey(): void
    {
        $this->store->save($this->examSurveyId, 7, $this->result(8.0), self::DIGEST, self::NOW);
        $this->store->save($this->examSurveyId, 8, $this->result(3.0), self::DIGEST, self::NOW);

        $this->assertSame(8.0, $this->store->find($this->examSurveyId, 7)['score']);
        $this->assertSame(3.0, $this->store->find($this->examSurveyId, 8)['score']);
        $this->assertNull($this->store->find($this->examSurveyId + 1, 7));
    }

    /**
     * 存的是哪一版答案键判出来的。答案键改过之后，旧成绩要能被认出来是旧的——
     * 否则一场考试改了答案键，前后两批人的分数没法比，也查不出为什么。
     */
    public function testTheKeyDigestIsKeptSoStaleScoresCanBeFound(): void
    {
        $this->store->save($this->examSurveyId, 7, $this->result(), self::DIGEST, self::NOW);

        $stale = $this->store->gradedWithOtherKey($this->examSurveyId, str_repeat('0', 64));

        $this->assertSame([7], $stale);
        $this->assertSame([], $this->store->gradedWithOtherKey($this->examSurveyId, self::DIGEST));
    }
}
