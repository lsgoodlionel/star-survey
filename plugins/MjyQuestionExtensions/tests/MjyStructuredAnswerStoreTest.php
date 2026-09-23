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
    private const STRUCTURE_V1 = 'rt1';
    private const STRUCTURE_V2 = 'rt2';

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
        \App()->getDb()->createCommand()->delete(
            self::$store->stateTableName(),
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
            self::STRUCTURE_V1,
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
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, [
            ['item' => '甲', 'qty' => '2'],
            ['item' => '乙', 'qty' => '3'],
        ]);

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, [
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
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, $rows);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, $rows);

        // 一行两列 = 两个单元格；第二次写入不得翻倍。
        $this->assertSame(2, $this->countCells(self::GENERATION_A, 7));
    }

    public function testGenerationSeparatesResponsesThatReuseTheSameId()
    {
        // 停用再激活后 response id 从 1 重新计数：旧代次的数据不能被覆盖也不能被读到。
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 1, self::QUESTION_CODE, self::STRUCTURE_V1, [['item' => '旧']]);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_B, 1, self::QUESTION_CODE, self::STRUCTURE_V1, [['item' => '新']]);

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
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, [['item' => '甲']]);

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, []);

        $this->assertSame([], self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE));
    }

    public function testPurgeResponseRemovesEveryQuestionOfThatResponseOnly()
    {
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, [['item' => '甲']]);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, 'QOTHER', self::STRUCTURE_V1, [['item' => '乙']]);
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 8, self::QUESTION_CODE, self::STRUCTURE_V1, [['item' => '丙']]);

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

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 9, self::QUESTION_CODE, self::STRUCTURE_V1, $rows);

        $this->assertSame($rows, self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 9, self::QUESTION_CODE));
    }

    // ------------------------------------------------- 结构版本（契约 v1 第二节）

    public function testEveryCellCarriesTheDeclaredStructureVersion()
    {
        self::$store->replaceRows(
            self::SURVEY_ID,
            self::GENERATION_A,
            7,
            self::QUESTION_CODE,
            self::STRUCTURE_V1,
            [['item' => '甲', 'qty' => '2']]
        );

        $this->assertSame(
            [self::STRUCTURE_V1],
            self::$store->fetchStructureVersions(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
    }

    public function testFetchRowsCanBeRestrictedToOneStructureVersion()
    {
        $rows = [['item' => '甲']];
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, $rows);

        $this->assertSame(
            $rows,
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1)
        );
        $this->assertSame(
            [],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V2)
        );
    }

    public function testRewritingAnAnswerUnderANewStructureVersionLeavesNoCellOfTheOldOne()
    {
        // 引擎答卷列里只有一份信封，所以一条答卷的一道题只能有一个当前结构版本。
        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V1, [
            ['item' => '旧', 'qty' => '1'],
        ]);

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, self::STRUCTURE_V2, [
            ['item' => '新', 'note' => 'n'],
        ]);

        $this->assertSame(
            [self::STRUCTURE_V2],
            self::$store->fetchStructureVersions(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
        $this->assertSame(
            [['item' => '新', 'note' => 'n']],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
    }

    public function testStateRecordsTheStructureVersionOfTheAnswerItJudged()
    {
        self::$store->recordAnswer(
            self::SURVEY_ID,
            self::GENERATION_A,
            7,
            self::QUESTION_CODE,
            self::STRUCTURE_V2,
            \MjyValidationResult::valid([['item' => '甲']])
        );

        $state = self::$store->fetchState(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE);
        $this->assertSame(self::STRUCTURE_V2, (string) $state[\MjyStructuredAnswerStore::STRUCTURE_VERSION_COLUMN]);
        $this->assertSame(1, (int) $state['row_count']);
    }

    public function testRejectedAnswersKeepTheStructureVersionButStoreNoCells()
    {
        self::$store->recordAnswer(
            self::SURVEY_ID,
            self::GENERATION_A,
            7,
            self::QUESTION_CODE,
            self::STRUCTURE_V1,
            \MjyValidationResult::invalid(['第 1 行缺少必填项'])
        );

        $state = self::$store->fetchState(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE);
        $this->assertSame(0, (int) $state['is_valid']);
        $this->assertSame(self::STRUCTURE_V1, (string) $state[\MjyStructuredAnswerStore::STRUCTURE_VERSION_COLUMN]);
        $this->assertSame(0, $this->countCells(self::GENERATION_A, 7));
    }

    /**
     * 结构版本来自题目属性，也就是来自 .lss，属于外部数据：不合法一律回落到
     * 「不知道是哪一版」，绝不猜一个版本号（失败关闭，契约 v1 第二节）。
     */
    public function testATamperedStructureVersionFallsBackToTheUnknownSentinel()
    {
        $legacy = \MjyStructuredAnswerStore::LEGACY_STRUCTURE_VERSION;

        $this->assertSame('rt1', \MjyStructuredAnswerStore::normaliseStructureVersion(' rt1 '));
        $this->assertSame($legacy, \MjyStructuredAnswerStore::normaliseStructureVersion(null));
        $this->assertSame($legacy, \MjyStructuredAnswerStore::normaliseStructureVersion(''));
        $this->assertSame($legacy, \MjyStructuredAnswerStore::normaliseStructureVersion("rt1'; DROP TABLE x"));
        $this->assertSame($legacy, \MjyStructuredAnswerStore::normaliseStructureVersion('_leading'));
        $this->assertSame($legacy, \MjyStructuredAnswerStore::normaliseStructureVersion(str_repeat('v', 33)));

        self::$store->replaceRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE, '坏值', [['item' => '甲']]);
        $this->assertSame(
            [$legacy],
            self::$store->fetchStructureVersions(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
    }

    /**
     * 迁移路径：契约出现之前建的表没有这一列，ensureSchema() 必须补上并把既有行
     * 标成「不知道是哪一版」，而且旧行仍然读得回来。
     *
     * 放在最后：它会临时删掉一列。
     */
    public function testEnsureSchemaAddsTheStructureVersionColumnToLegacyTablesAndBackfillsIt()
    {
        $db = \App()->getDb();
        $column = \MjyStructuredAnswerStore::STRUCTURE_VERSION_COLUMN;
        foreach ([self::$store->tableName(), self::$store->stateTableName()] as $table) {
            $db->createCommand()->dropColumn($table, $column);
        }
        $db->getSchema()->refresh();
        $db->createCommand()->insert(self::$store->tableName(), [
            'engine_instance_id' => 'test-instance',
            'survey_id' => self::SURVEY_ID,
            'generation' => self::GENERATION_A,
            'response_id' => 7,
            'question_code' => self::QUESTION_CODE,
            'row_index' => 0,
            'column_code' => 'item',
            'cell_value' => '契约之前写下的',
            'updated_at' => gmdate('Y-m-d H:i:s'),
        ]);

        $upgraded = self::$store->upgradeSchema();

        $this->assertSame([self::$store->tableName(), self::$store->stateTableName()], $upgraded);
        $this->assertSame(
            [\MjyStructuredAnswerStore::LEGACY_STRUCTURE_VERSION],
            self::$store->fetchStructureVersions(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
        $this->assertSame(
            [['item' => '契约之前写下的']],
            self::$store->fetchRows(self::SURVEY_ID, self::GENERATION_A, 7, self::QUESTION_CODE)
        );
        // 幂等：列已经在了就什么都不做。
        $this->assertSame([], self::$store->upgradeSchema());
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
