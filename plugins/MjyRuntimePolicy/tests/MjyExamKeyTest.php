<?php

namespace ls\tests;

/**
 * 考试答案键的解析（WP-09.1，契约 survey-exam-v1 §4）。
 *
 * 答案键是**服务端独有**的东西：它从 plugin_settings 读进来，判分在服务端做完，
 * 判分结果之外的任何一点都不出插件。本文件只管"读进来的这份载荷是不是可信的"，
 * 不可信就整份拒绝——半份答案键会让一部分题变成"怎么答都对"。
 */
class MjyExamKeyTest extends TestBaseClass
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
     * 网关编译出来的规范载荷。
     */
    private function payload(array $overrides = []): string
    {
        $entries = $overrides['answerKey'] ?? [
            ['question' => 'QSINGLE', 'kind' => 'choice', 'correct' => ['A2'], 'points' => 5.0],
            ['question' => 'QMULTI', 'kind' => 'set', 'correct' => ['SQ001', 'SQ003'], 'points' => 4.0],
            [
                'question' => 'QTEXT', 'kind' => 'text', 'correct' => ['PARIS'], 'points' => 3.0,
                'match' => ['ignoreCase' => true, 'trim' => true],
            ],
        ];
        $payload = ['schema' => 'mjy-exam-key/1', 'answerKey' => $entries];
        foreach ($overrides as $name => $value) {
            if ($name !== 'answerKey') {
                $payload[$name] = $value;
            }
        }
        return json_encode($payload, JSON_UNESCAPED_SLASHES);
    }

    public function testParsesTheCanonicalPayload(): void
    {
        $key = \MjyExamKey::fromJson($this->payload());

        $this->assertSame(['QSINGLE', 'QMULTI', 'QTEXT'], $key->questionCodes());
        $this->assertSame(12.0, $key->totalPoints());
    }

    public function testEntryCarriesItsAnswersAndPoints(): void
    {
        $entry = \MjyExamKey::fromJson($this->payload())->find('QSINGLE');

        $this->assertNotNull($entry);
        $this->assertSame('choice', $entry->kind());
        $this->assertSame(['A2'], $entry->correct());
        $this->assertSame(5.0, $entry->points());
    }

    public function testTextEntryCarriesItsMatchingRules(): void
    {
        $entry = \MjyExamKey::fromJson($this->payload())->find('QTEXT');

        $this->assertTrue($entry->ignoreCase());
        $this->assertTrue($entry->trim());
    }

    public function testChoiceEntryDefaultsToExactMatching(): void
    {
        $entry = \MjyExamKey::fromJson($this->payload())->find('QSINGLE');

        $this->assertFalse($entry->ignoreCase());
    }

    public function testUnknownQuestionHasNoEntry(): void
    {
        $this->assertNull(\MjyExamKey::fromJson($this->payload())->find('QNOPE'));
    }

    public function testDigestIsTheSha256OfThePayload(): void
    {
        $text = $this->payload();

        $this->assertSame(hash('sha256', $text), \MjyExamKey::fromJson($text)->digest());
    }

    /**
     * @dataProvider malformedPayloads
     */
    public function testMalformedPayloadIsRejected(string $description, string $text): void
    {
        $this->expectException(\InvalidArgumentException::class);
        \MjyExamKey::fromJson($text);
    }

    public function malformedPayloads(): array
    {
        $entry = ['question' => 'Q1', 'kind' => 'choice', 'correct' => ['A1'], 'points' => 1.0];
        $with = function (array $changes) use ($entry): string {
            return json_encode(['schema' => 'mjy-exam-key/1', 'answerKey' => [array_merge($entry, $changes)]]);
        };
        return [
            '不是 JSON' => ['不是 JSON', '{'],
            '不是对象' => ['不是对象', '[]'],
            '架构标记不对' => ['架构标记不对', json_encode(['schema' => 'mjy-exam-key/2', 'answerKey' => [$entry]])],
            '缺架构标记' => ['缺架构标记', json_encode(['answerKey' => [$entry]])],
            'answerKey 不是数组' => ['answerKey 不是数组', json_encode(['schema' => 'mjy-exam-key/1', 'answerKey' => 1])],
            'answerKey 为空' => ['answerKey 为空', json_encode(['schema' => 'mjy-exam-key/1', 'answerKey' => []])],
            '题目代码为空' => ['题目代码为空', $with(['question' => ''])],
            '题型不认识' => ['题型不认识', $with(['kind' => 'matrix'])],
            '正确答案为空' => ['正确答案为空', $with(['correct' => []])],
            '正确答案不是字符串' => ['正确答案不是字符串', $with(['correct' => [1]])],
            '分值不是数字' => ['分值不是数字', $with(['points' => 'five'])],
            '分值不是正数' => ['分值不是正数', $with(['points' => 0])],
            '同一题出现两次' => [
                '同一题出现两次',
                json_encode(['schema' => 'mjy-exam-key/1', 'answerKey' => [$entry, $entry]]),
            ],
        ];
    }

    /**
     * 未知键不能被静默忽略：网关加了新语义而插件版本旧，宁可整份拒绝，
     * 也不要按旧语义判出一份看起来正常的成绩。
     */
    public function testUnknownKeyIsRejected(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        \MjyExamKey::fromJson($this->payload(['partialCredit' => 'linear']));
    }
}
