<?php

namespace ls\tests;

/**
 * P0-00.3：结构化副表的契约。
 *
 * 自然键沿用 [ADR 0003] 的约定：(引擎实例, 问卷, 代次, 答卷 id, 题目代码)，
 * 再加上行序号与列代码。代次来自 MjyPlatformBridge 的代次表，因为答卷 id 在
 * 问卷重新激活后会重复，只用 (sid, response_id) 会把新旧答卷混在一起。
 */
class MjyStructuredAnswerStoreTest extends TestBaseClass
{
    private const SURVEY_ID = 991001;
    private const GENERATION_A = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa';
    private const GENERATION_B = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb';
    private const QUESTION_CODE = 'QTABLE';

    /** @var \MjyStructuredAnswerStore */
    private static $store;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$store = new \MjyStructuredAnswerStore(\App()->getDb(), 'test-instance');
        self::$store->ensureSchema();
    }

    protected function setUp(): void
    {
        parent::setUp();
        \App()->getDb()->createCommand()->delete(
            self::$store->tableName(),
            'survey_id = :sid',
            [':sid' => self::SURVEY_ID]
        );
    }

    public function testRowsAreStoredOneCellPerRecord()
    {
        $written = self::$store->replaceRows(
            self::SURVEY_ID,
            self::GENERATION_A,
            7,
            self::QUESTION_CODE,
            [['item' => '甲', 'qty' => '2'], ['item' => '乙', 'qty' => '3']]
        );

        $this->assertSame(4, $written);
        $this->assertSame(
            [['item' => '甲', 'qty' => '2'], ['item' => '乙', 'qty' => '3']],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
    }

    public function testReplacingAnAnswerRemovesTheRowsThatDisappeared()
    {
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, [
            ['item' => '甲', 'qty' => '2'],
            ['item' => '乙', 'qty' => '3'],
        ]);

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, [
            ['item' => '丙', 'qty' => '1'],
        ]);

        $this->assertSame(
            [['item' => '丙', 'qty' => '1']],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
    }

    public function testWritingTheSameAnswerTwiceIsIdempotent()
    {
        $rows = [['item' => '甲', 'qty' => '2']];
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, $rows);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, $rows);

        // 一行两列 = 两个单元格；第二次写入不得翻倍。
        $this->assertSame(2, $this->countCells(self::GENERATION_A, 7));
    }

    public function testGenerationSeparatesResponsesThatReuseTheSameId()
    {
        // 停用再激活后 response id 从 1 重新计数：旧代次的数据不能被覆盖也不能被读到。
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 1, self::QUESTION_CODE, [['item' => '旧']]);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_B, 1, self::QUESTION_CODE, [['item' => '新']]);

        $this->assertSame(
            [['item' => '旧']],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 1, self::QUESTION_CODE)
        );
        $this->assertSame(
            [['item' => '新']],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_B, 1, self::QUESTION_CODE)
        );
    }

    public function testEmptyAnswerClearsTheSideTable()
    {
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, [['item' => '甲']]);

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, []);

        $this->assertSame([], self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE));
    }

    public function testPurgeResponseRemovesEveryQuestionOfThatResponseOnly()
    {
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, [['item' => '甲']]);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, 'QOTHER', [['item' => '乙']]);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 8, self::QUESTION_CODE, [['item' => '丙']]);

        self::$store->purgeResponse(self::SURVEY_ID, self::GENERATION_A, 7);

        $this->assertSame(0, $this->countCells(self::GENERATION_A, 7));
        $this->assertSame(1, $this->countCells(self::GENERATION_A, 8));
    }

    public function testRowOrderSurvivesDoubleDigitRowCounts()
    {
        $rows = [];
        for ($index = 0; $index < 12; $index++) {
            $rows[] = ['item' => 'R' . $index];
        }

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 9, self::QUESTION_CODE, $rows);

        $this->assertSame($rows, self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 9, self::QUESTION_CODE));
    }

    private function countCells(string $generation, int $responseId): int
    {
        return (int) \App()->getDb()->createCommand()
            ->select('COUNT(*)')
            ->from(self::$store->tableName())
            ->where(
                'survey_id = :sid AND generation = :gen AND response_id = :rid',
                [':sid' => self::SURVEY_ID, ':gen' => $generation, ':rid' => $responseId]
            )
            ->queryScalar();
    }
}
