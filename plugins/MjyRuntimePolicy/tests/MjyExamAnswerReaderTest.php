<?php

namespace ls\tests;

/**
 * 从答卷行里读出"这道题作答者填了什么"（WP-10）。
 *
 * 引擎的答卷表按字段名存（`<sid>X<gid>X<qid>`，多选还要再带子题代码），
 * 答案键按**题目代码**说话。中间这一层换算走引擎自己的 createFieldMap：
 * 字段名会随实例、导入、重新启用而变，题目代码不会（与契约
 * question-extension-tables-v1 里"键是题目代码而不是字段名"同一个理由）。
 *
 * 字段映射由调用方注入，所以这个类能脱离引擎单测。
 */
class MjyExamAnswerReaderTest extends TestBaseClass
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

    /** createFieldMap 的形状：字段名 → {title: 题目代码, aid: 子题代码, type} */
    private function fieldMap(): array
    {
        return [
            'id' => ['fieldname' => 'id', 'title' => '', 'aid' => '', 'type' => 'id'],
            'submitdate' => ['fieldname' => 'submitdate', 'title' => '', 'aid' => '', 'type' => 'submitdate'],
            '123X1X4' => ['fieldname' => '123X1X4', 'title' => 'QPICK', 'aid' => '', 'type' => 'L'],
            '123X1X5' => ['fieldname' => '123X1X5', 'title' => 'QCITY', 'aid' => '', 'type' => 'S'],
            '123X1X6SQ001' => ['fieldname' => '123X1X6SQ001', 'title' => 'QMULTI', 'aid' => 'SQ001', 'type' => 'M'],
            '123X1X6SQ002' => ['fieldname' => '123X1X6SQ002', 'title' => 'QMULTI', 'aid' => 'SQ002', 'type' => 'M'],
            '123X1X6SQ003' => ['fieldname' => '123X1X6SQ003', 'title' => 'QMULTI', 'aid' => 'SQ003', 'type' => 'M'],
        ];
    }

    private function read(array $row, array $codes = ['QPICK', 'QCITY', 'QMULTI']): array
    {
        return (new \MjyExamAnswerReader($this->fieldMap()))->read($row, $codes);
    }

    public function testASingleChoiceReadsItsCode(): void
    {
        $answers = $this->read(['123X1X4' => 'AO01']);

        $this->assertSame('AO01', $answers['QPICK']);
    }

    public function testATextAnswerReadsItsText(): void
    {
        $answers = $this->read(['123X1X5' => '  巴黎 ']);

        $this->assertSame('  巴黎 ', $answers['QCITY'], '原样读出来，修剪归判分那一步');
    }

    /** 多选：选中的是值为 "Y" 的那几列，读出来是子题代码的集合。 */
    public function testAMultipleChoiceReadsTheSelectedSubquestionCodes(): void
    {
        $answers = $this->read([
            '123X1X6SQ001' => 'Y', '123X1X6SQ002' => '', '123X1X6SQ003' => 'Y',
        ]);

        $this->assertSame(['SQ001', 'SQ003'], $answers['QMULTI']);
    }

    public function testAnUnansweredMultipleChoiceIsAnEmptySet(): void
    {
        $answers = $this->read(['123X1X6SQ001' => '', '123X1X6SQ002' => '', '123X1X6SQ003' => '']);

        $this->assertSame([], $answers['QMULTI']);
    }

    public function testAMissingColumnReadsAsUnanswered(): void
    {
        $answers = $this->read([]);

        $this->assertSame('', $answers['QPICK']);
        $this->assertSame([], $answers['QMULTI']);
    }

    /** 只读答案键点名的那几道题：别的题的答案与判分无关，读它只会把成绩表撑大。 */
    public function testOnlyTheRequestedQuestionsAreRead(): void
    {
        $answers = $this->read(['123X1X4' => 'AO01', '123X1X5' => 'x'], ['QPICK']);

        $this->assertSame(['QPICK'], array_keys($answers));
    }

    public function testAQuestionThatIsNotInTheFieldMapIsSkipped(): void
    {
        $answers = $this->read(['123X1X4' => 'AO01'], ['QPICK', 'QGONE']);

        $this->assertSame(['QPICK'], array_keys($answers));
    }

    /** 固有列（id、submitdate）的 title 是空的，不能被当成某道题的答案。 */
    public function testMetaColumnsAreNeverMistakenForAnswers(): void
    {
        $answers = $this->read(['id' => '7', 'submitdate' => '2026-09-24 10:00:00'], ['QPICK']);

        $this->assertSame('', $answers['QPICK']);
    }
}
