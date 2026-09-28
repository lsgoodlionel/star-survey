<?php

namespace ls\tests;

/**
 * 客观题自动评分（WP-10）。
 *
 * 判分**只在服务端做**：输入是平台下发的答案键（MjyExamKey，只存在于
 * plugin_settings 里）与作答者写进答卷表的答案。作答者提交不了"我答对了"——
 * 答卷表里根本没有可以放对错的列，对错是这里按两边推出来的。
 *
 * 判分本身是纯函数：没有数据库、没有会话、没有时钟。所以它能被穷举着测。
 */
class MjyExamGraderTest extends TestBaseClass
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

    private function key(array $entries): \MjyExamKey
    {
        return \MjyExamKey::fromJson(json_encode(['schema' => 'mjy-exam-key/1', 'answerKey' => $entries]));
    }

    private function fullKey(): \MjyExamKey
    {
        return $this->key([
            ['question' => 'QPICK', 'kind' => 'choice', 'correct' => ['AO01'], 'points' => 5.0],
            ['question' => 'QMULTI', 'kind' => 'set', 'correct' => ['SQ001', 'SQ003'], 'points' => 4.0],
            [
                'question' => 'QCITY', 'kind' => 'text', 'correct' => ['Paris', '巴黎'], 'points' => 3.0,
                'match' => ['ignoreCase' => true, 'trim' => true],
            ],
        ]);
    }

    private function grade(\MjyExamKey $key, array $answers): \MjyExamResult
    {
        return (new \MjyExamGrader())->grade($key, $answers);
    }

    // ------------------------------------------------------------ 总体

    public function testAPerfectPaperScoresEverything(): void
    {
        $result = $this->grade($this->fullKey(), [
            'QPICK' => 'AO01', 'QMULTI' => ['SQ001', 'SQ003'], 'QCITY' => 'paris',
        ]);

        $this->assertSame(12.0, $result->score());
        $this->assertSame(12.0, $result->maxScore());
        $this->assertSame(3, $result->correctCount());
        $this->assertSame(3, $result->questionCount());
    }

    public function testAnEmptyPaperScoresZeroButStillHasAMaximum(): void
    {
        $result = $this->grade($this->fullKey(), []);

        $this->assertSame(0.0, $result->score());
        $this->assertSame(12.0, $result->maxScore());
        $this->assertSame(0, $result->correctCount());
    }

    public function testEveryKeyedQuestionAppearsInTheDetail(): void
    {
        $result = $this->grade($this->fullKey(), ['QPICK' => 'AO01']);

        $detail = $result->detail();
        $this->assertSame(['QPICK', 'QMULTI', 'QCITY'], array_keys($detail));
        $this->assertTrue($detail['QPICK']['correct']);
        $this->assertSame(5.0, $detail['QPICK']['points']);
        $this->assertFalse($detail['QMULTI']['correct']);
        $this->assertSame(0.0, $detail['QMULTI']['points']);
    }

    /** 明细里不许出现正确答案：它会被存进成绩表，而成绩是要给人看的。 */
    public function testTheDetailNeverCarriesTheCorrectAnswer(): void
    {
        $result = $this->grade($this->fullKey(), ['QCITY' => 'Lyon']);

        $encoded = json_encode($result->detail(), JSON_UNESCAPED_UNICODE);
        $this->assertStringNotContainsString('Paris', $encoded);
        $this->assertStringNotContainsString('巴黎', $encoded);
        $this->assertStringNotContainsString('AO01', $encoded);
        $this->assertStringNotContainsString('SQ003', $encoded);
    }

    // ------------------------------------------------------------ 单选

    public function testASingleChoiceIsExactMatch(): void
    {
        $key = $this->key([['question' => 'Q', 'kind' => 'choice', 'correct' => ['AO01'], 'points' => 5.0]]);

        $this->assertSame(5.0, $this->grade($key, ['Q' => 'AO01'])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => 'AO02'])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => 'ao01'])->score(), '选项代码不做大小写折叠');
        $this->assertSame(0.0, $this->grade($key, ['Q' => ''])->score());
    }

    // ------------------------------------------------------------ 多选

    public function testAMultipleChoiceNeedsTheWholeSetAndNothingMore(): void
    {
        $key = $this->key([
            ['question' => 'Q', 'kind' => 'set', 'correct' => ['SQ001', 'SQ003'], 'points' => 4.0],
        ]);

        $this->assertSame(4.0, $this->grade($key, ['Q' => ['SQ001', 'SQ003']])->score());
        $this->assertSame(4.0, $this->grade($key, ['Q' => ['SQ003', 'SQ001']])->score(), '顺序无关');
        $this->assertSame(0.0, $this->grade($key, ['Q' => ['SQ001']])->score(), '少选不给分');
        $this->assertSame(0.0, $this->grade($key, ['Q' => ['SQ001', 'SQ003', 'SQ002']])->score(), '多选不给分');
        $this->assertSame(0.0, $this->grade($key, ['Q' => []])->score());
    }

    // ------------------------------------------------------------ 文本

    public function testTextMatchingFollowsTheDeclaredRules(): void
    {
        $key = $this->key([[
            'question' => 'Q', 'kind' => 'text', 'correct' => ['Paris'], 'points' => 3.0,
            'match' => ['ignoreCase' => true, 'trim' => true],
        ]]);

        $this->assertSame(3.0, $this->grade($key, ['Q' => '  PARIS '])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => 'Par is'])->score());
    }

    public function testCaseSensitiveTextRejectsAWrongCase(): void
    {
        $key = $this->key([[
            'question' => 'Q', 'kind' => 'text', 'correct' => ['Paris'], 'points' => 3.0,
            'match' => ['ignoreCase' => false, 'trim' => true],
        ]]);

        $this->assertSame(3.0, $this->grade($key, ['Q' => 'Paris'])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => 'paris'])->score());
    }

    public function testWhitespaceIsKeptWhenTrimIsOff(): void
    {
        $key = $this->key([[
            'question' => 'Q', 'kind' => 'text', 'correct' => ['Paris'], 'points' => 3.0,
            'match' => ['ignoreCase' => false, 'trim' => false],
        ]]);

        $this->assertSame(0.0, $this->grade($key, ['Q' => ' Paris'])->score());
    }

    public function testAnyOfTheAcceptedSpellingsCounts(): void
    {
        $key = $this->key([[
            'question' => 'Q', 'kind' => 'text', 'correct' => ['Paris', '巴黎'], 'points' => 3.0,
            'match' => ['ignoreCase' => true, 'trim' => true],
        ]]);

        $this->assertSame(3.0, $this->grade($key, ['Q' => '巴黎'])->score());
    }

    // ------------------------------------------------------------ 数值

    public function testNumbersCompareNumericallyNotAsText(): void
    {
        $key = $this->key([['question' => 'Q', 'kind' => 'number', 'correct' => ['3'], 'points' => 2.0]]);

        $this->assertSame(2.0, $this->grade($key, ['Q' => '3'])->score());
        $this->assertSame(2.0, $this->grade($key, ['Q' => '3.0'])->score(), '3.0 就是 3');
        $this->assertSame(2.0, $this->grade($key, ['Q' => ' 3 '])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => '30'])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => ''])->score());
        $this->assertSame(0.0, $this->grade($key, ['Q' => '三'])->score());
    }

    // ------------------------------------------------------------ 作答者送来的垃圾

    /**
     * 答案来自答卷表，而答卷表的内容归根到底是作答者写的。判分不能被它搞崩。
     */
    public function testARespondentCannotBreakGradingWithAWeirdValue(): void
    {
        $key = $this->key([
            ['question' => 'QPICK', 'kind' => 'choice', 'correct' => ['AO01'], 'points' => 5.0],
            ['question' => 'QMULTI', 'kind' => 'set', 'correct' => ['SQ001'], 'points' => 4.0],
        ]);

        // 单选题收到数组、多选题收到字符串：都当作没答对，不抛异常。
        $result = $this->grade($key, ['QPICK' => ['AO01'], 'QMULTI' => 'SQ001']);

        $this->assertSame(0.0, $result->score());
        $this->assertSame(0, $result->correctCount());
    }

    /** 答案键里没有的题，作答者答了也不计分——分值总和必须只由答案键决定。 */
    public function testAnswersToUnkeyedQuestionsAreIgnored(): void
    {
        $key = $this->key([['question' => 'Q', 'kind' => 'choice', 'correct' => ['AO01'], 'points' => 5.0]]);

        $result = $this->grade($key, ['Q' => 'AO01', 'QOTHER' => 'anything']);

        $this->assertSame(5.0, $result->score());
        $this->assertSame(5.0, $result->maxScore());
        $this->assertSame(1, $result->questionCount());
    }
}
