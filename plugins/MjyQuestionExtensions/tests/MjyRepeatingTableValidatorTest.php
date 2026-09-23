<?php

namespace ls\tests;

/**
 * P0-00.3：自增表格的服务端校验。
 *
 * 引擎没有「自增表格」题型，平台的方案是「基础题（长文本）＋题型主题＋结构化副表」。
 * 副表里的每一行都来自这里的归一化结果，所以校验器必须是唯一的真相来源：
 * 浏览器里的 JS 只负责编辑体验，提交上来的 JSON 一律重新解析、重新校验、重新归一化。
 */
class MjyRepeatingTableValidatorTest extends TestBaseClass
{
    private const SPEC_JSON = '[
        {"code":"item","label":"项目","type":"text","required":true,"maxLength":20},
        {"code":"qty","label":"数量","type":"integer","min":1,"max":99},
        {"code":"note","label":"备注","type":"text"}
    ]';

    public function testValidPayloadIsAcceptedAndNormalised()
    {
        $result = $this->validate('{"v":1,"rows":[{"qty":"2","item":"  设备甲  ","note":""}]}');

        $this->assertTrue($result->isValid(), implode('; ', $result->errors()));
        $this->assertSame([['item' => '设备甲', 'qty' => '2', 'note' => '']], $result->rows());
        // 归一化后列顺序按规格排列，前后空白去掉，中文不转义。
        $this->assertSame(
            '{"v":1,"rows":[{"item":"设备甲","qty":"2","note":""}]}',
            $result->normalisedJson()
        );
    }

    public function testEmptyAnswerIsValidWhenNoMinimumIsRequired()
    {
        $result = $this->validate('', 0, 5);

        $this->assertTrue($result->isValid());
        $this->assertSame([], $result->rows());
        $this->assertSame('{"v":1,"rows":[]}', $result->normalisedJson());
    }

    public function testEmptyAnswerIsRejectedWhenMinimumRowsRequired()
    {
        $result = $this->validate(null, 1, 5);

        $this->assertFalse($result->isValid());
        $this->assertSame([], $result->rows());
    }

    public function testBrokenJsonIsRejectedInsteadOfSilentlyStored()
    {
        $result = $this->validate('{"v":1,"rows":[{"item":');

        $this->assertFalse($result->isValid());
        $this->assertNotEmpty($result->errors());
    }

    public function testWrongEnvelopeVersionIsRejected()
    {
        $result = $this->validate('{"v":2,"rows":[]}');

        $this->assertFalse($result->isValid());
    }

    public function testRowsMustBeAList()
    {
        $result = $this->validate('{"v":1,"rows":{"0":{"item":"x"}}}');

        $this->assertFalse($result->isValid());
    }

    public function testTooManyRowsAreRejected()
    {
        $rows = [];
        for ($index = 0; $index < 4; $index++) {
            $rows[] = ['item' => 'x' . $index];
        }
        $result = $this->validate(json_encode(['v' => 1, 'rows' => $rows]), 1, 3);

        $this->assertFalse($result->isValid());
    }

    public function testUnknownColumnIsRejectedRatherThanDropped()
    {
        // 悄悄丢弃未知列会让平台和引擎的列字典无声漂移，必须报错。
        $result = $this->validate('{"v":1,"rows":[{"item":"x","ghost":"1"}]}');

        $this->assertFalse($result->isValid());
    }

    public function testRequiredColumnCannotBeEmpty()
    {
        $result = $this->validate('{"v":1,"rows":[{"item":"  ","qty":"1"}]}');

        $this->assertFalse($result->isValid());
    }

    public function testIntegerColumnRejectsNonIntegerAndOutOfRangeValues()
    {
        $this->assertFalse($this->validate('{"v":1,"rows":[{"item":"x","qty":"1.5"}]}')->isValid());
        $this->assertFalse($this->validate('{"v":1,"rows":[{"item":"x","qty":"0"}]}')->isValid());
        $this->assertFalse($this->validate('{"v":1,"rows":[{"item":"x","qty":"100"}]}')->isValid());
        $this->assertTrue($this->validate('{"v":1,"rows":[{"item":"x","qty":"99"}]}')->isValid());
    }

    public function testMaxLengthIsEnforcedInCharactersNotBytes()
    {
        // 20 个中文字符合法，21 个不合法：按字符数而不是字节数。
        $this->assertTrue($this->validate($this->rowsWith(str_repeat('中', 20)))->isValid());
        $this->assertFalse($this->validate($this->rowsWith(str_repeat('中', 21)))->isValid());
    }

    public function testScalarsAreCoercedToStringsButStructuresAreRejected()
    {
        $this->assertTrue($this->validate('{"v":1,"rows":[{"item":"x","qty":3}]}')->isValid());
        $this->assertFalse($this->validate('{"v":1,"rows":[{"item":["x"]}]}')->isValid());
    }

    public function testColumnSpecItselfIsValidated()
    {
        $this->expectException(\InvalidArgumentException::class);
        \MjyTableColumnSpec::fromJson('[{"label":"没有 code"}]');
    }

    public function testDuplicateColumnCodesAreRejected()
    {
        $this->expectException(\InvalidArgumentException::class);
        \MjyTableColumnSpec::fromJson('[{"code":"a","type":"text"},{"code":"a","type":"text"}]');
    }

    public function testUnknownColumnNameNeverCarriesMarkupIntoTheErrorText()
    {
        // 未知列名完全来自作答者，而错误文案最终会被引擎以 raw 方式渲染
        // （valid_message_and_help.twig 的 `man_message | raw`），所以必须在源头消毒。
        $result = $this->validate('{"v":1,"rows":[{"item":"x","<img src=x onerror=alert(1)>":"1"}]}');

        $this->assertFalse($result->isValid());
        // 只保留字母数字下划线连字符：标签属性、引号、括号一个都过不去。
        $this->assertStringNotContainsString('<', $result->errorText());
        $this->assertStringNotContainsString('>', $result->errorText());
        $this->assertStringNotContainsString('=', $result->errorText());
        $this->assertStringNotContainsString('(', $result->errorText());
        $this->assertStringNotContainsString('"', $result->errorText());
    }

    public function testRowCountIsCappedEvenWhenTheQuestionDeclaresNoMaximum()
    {
        // 属性缺失时必须失败关闭（回落到默认上限），不能变成「无限行」。
        $rows = [];
        for ($index = 0; $index <= \MjyRepeatingTableValidator::DEFAULT_MAX_ROWS; $index++) {
            $rows[] = ['item' => 'x' . $index];
        }

        $result = $this->validate(json_encode(['v' => 1, 'rows' => $rows]), 0, 0);

        $this->assertFalse($result->isValid());
    }

    public function testConfiguredMaximumCannotExceedTheHardCap()
    {
        $rows = [];
        for ($index = 0; $index <= \MjyRepeatingTableValidator::HARD_MAX_ROWS; $index++) {
            $rows[] = ['item' => 'x' . $index];
        }

        $result = $this->validate(json_encode(['v' => 1, 'rows' => $rows]), 0, 100000);

        $this->assertFalse($result->isValid());
    }

    public function testOversizedPayloadIsRejectedBeforeItIsDecoded()
    {
        $huge = '{"v":1,"rows":[{"item":"' . str_repeat('x', \MjyRepeatingTableValidator::MAX_PAYLOAD_LENGTH) . '"}]}';

        $result = $this->validate($huge);

        $this->assertFalse($result->isValid());
    }

    // ------------------------------------------------------------ 枚举列与唯一列（R02-11）

    /**
     * 枚举列的取值集合由平台在发布时声明，作答者只能从里面挑。
     * 浏览器端渲染成下拉或按钮都不作数——提交上来的是字符串，闸门只能在这里。
     */
    public function testEnumCellOutsideTheDeclaredOptionsIsRejected()
    {
        $result = $this->validateScored('{"v":1,"rows":[{"target":"T1","service":"S9"}]}');

        $this->assertFalse($result->isValid());
        $this->assertNotEmpty($result->errors());
    }

    public function testEnumCellInsideTheDeclaredOptionsIsAccepted()
    {
        $result = $this->validateScored('{"v":1,"rows":[{"target":"T1","service":"S2"}]}');

        $this->assertTrue($result->isValid(), implode('; ', $result->errors()));
        $this->assertSame([['target' => 'T1', 'service' => 'S2']], $result->rows());
    }

    /**
     * 唯一列：同一个评价对象不能被评两次。行数被 min/max 钉死之后，
     * 「枚举＋唯一＋行数」合起来就逼出了一一对应，不必另写一套按行下标的规则。
     */
    public function testRepeatedValueInADistinctColumnIsRejected()
    {
        $result = $this->validateScored(
            '{"v":1,"rows":[{"target":"T1","service":"S1"},{"target":"T1","service":"S2"}]}'
        );

        $this->assertFalse($result->isValid());
        $this->assertNotEmpty($result->errors());
    }

    public function testDistinctColumnAcceptsDifferentValues()
    {
        $result = $this->validateScored(
            '{"v":1,"rows":[{"target":"T1","service":"S1"},{"target":"T2","service":"S2"}]}'
        );

        $this->assertTrue($result->isValid(), implode('; ', $result->errors()));
    }

    /** 空值不算重复：非必答的唯一列可以有多行留空。 */
    public function testEmptyValuesDoNotCollideInADistinctColumn()
    {
        $spec = '[{"code":"tag","type":"enum","options":["A","B"],"distinct":true}]';
        $validator = new \MjyRepeatingTableValidator(\MjyTableColumnSpec::fromJson($spec), 0, 10);

        $result = $validator->validate('{"v":1,"rows":[{"tag":""},{"tag":""}]}');

        $this->assertTrue($result->isValid(), implode('; ', $result->errors()));
    }

    public function testAnEnumColumnWithoutOptionsIsRefusedAtParseTime()
    {
        $this->expectException(\InvalidArgumentException::class);

        \MjyTableColumnSpec::fromJson('[{"code":"tag","type":"enum"}]');
    }

    private function rowsWith(string $item): string
    {
        return json_encode(['v' => 1, 'rows' => [['item' => $item]]], JSON_UNESCAPED_UNICODE);
    }

    private function validate(?string $answer, int $minRows = 0, int $maxRows = 10): \MjyValidationResult
    {
        $validator = new \MjyRepeatingTableValidator(
            \MjyTableColumnSpec::fromJson(self::SPEC_JSON),
            $minRows,
            $maxRows
        );
        return $validator->validate($answer);
    }

    /** 循环评价那一类的列形状：一列认对象（枚举＋唯一），一列存评分（枚举）。 */
    private function validateScored(?string $answer): \MjyValidationResult
    {
        $spec = '[
            {"code":"target","label":"评价对象","type":"enum","required":true,"distinct":true,
             "options":[{"code":"T1","label":"甲"},{"code":"T2","label":"乙"}]},
            {"code":"service","label":"服务","type":"enum","required":true,
             "options":[{"code":"S1","label":"差"},{"code":"S2","label":"好"}]}
        ]';
        $validator = new \MjyRepeatingTableValidator(\MjyTableColumnSpec::fromJson($spec), 1, 2);
        return $validator->validate($answer);
    }
}
