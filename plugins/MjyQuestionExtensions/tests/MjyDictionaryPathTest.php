<?php

namespace ls\tests;

/**
 * 多级下拉（R02-03）的**服务端路径判定**。
 *
 * 前端的级联只是方便：作答者可以关掉 JavaScript 直接往 textarea 里写任意 JSON 信封，
 * 也可以改 DOM 把别的省的市塞进第二级。所以「这条省/市/区路径真实存在」必须在服务端重算一遍，
 * 而且是在答案落库**之前**（beforeSurveyPage，ADR 0006 决定 3 的闸门二）。
 *
 * 本文件逐条钉住篡改用例：跨省的市、不存在的区、跨版本的节点、跳级、不从根开始。
 */
class MjyDictionaryPathTest extends TestBaseClass
{
    private const DICTIONARY = 'cn-admin-divisions';
    private const VERSION = '2024.1';
    private const OLD_VERSION = '2023.1';
    private const DIGEST = 'dg1:0123456789abcdef';

    private const COLUMNS = '[
        {"code":"L1","label":"省","type":"dict","required":true,
         "dictionary":"cn-admin-divisions","dictionaryVersion":"2024.1","level":1},
        {"code":"L2","label":"市","type":"dict","required":true,
         "dictionary":"cn-admin-divisions","dictionaryVersion":"2024.1","level":2},
        {"code":"L3","label":"区","type":"dict","required":true,
         "dictionary":"cn-admin-divisions","dictionaryVersion":"2024.1","level":3}
    ]';

    /** @var \MjyDictionaryStore */
    private static $store;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::$store = new \MjyDictionaryStore(\App()->getDb());
        self::$store->ensureSchema();
        self::$store->forget(self::DICTIONARY, self::VERSION);
        self::$store->forget(self::DICTIONARY, self::OLD_VERSION);
        self::$store->install(self::DICTIONARY, self::VERSION, self::DIGEST, [
            ['110000', '', '北京市'],
            ['110100', '110000', '市辖区'],
            ['110101', '110100', '东城区'],
            ['440000', '', '广东省'],
            ['440100', '440000', '广州市'],
            ['440103', '440100', '荔湾区'],
        ]);
        // 上一版里有个区在这一版被撤并掉了——「跨版本的节点」就拿它做。
        self::$store->install(self::DICTIONARY, self::OLD_VERSION, 'dg1:1111111111111111', [
            ['440000', '', '广东省'],
            ['440100', '440000', '广州市'],
            ['440104', '440100', '越秀区'],
        ]);
    }

    private function validate(string $answer, ?\MjyDictionaryStore $store = null): \MjyValidationResult
    {
        $validator = new \MjyRepeatingTableValidator(
            \MjyTableColumnSpec::fromJson(self::COLUMNS),
            1,
            1,
            func_num_args() > 1 ? $store : self::$store
        );
        return $validator->validate($answer);
    }

    private function envelope(string $l1, string $l2, string $l3): string
    {
        return json_encode(['v' => 1, 'rows' => [['L1' => $l1, 'L2' => $l2, 'L3' => $l3]]]);
    }

    public function testAGenuinePathIsAccepted()
    {
        $result = $this->validate($this->envelope('440000', '440100', '440103'));

        $this->assertTrue($result->isValid(), implode('; ', $result->errors()));
        $this->assertSame([['L1' => '440000', 'L2' => '440100', 'L3' => '440103']], $result->rows());
    }

    /** 篡改一：把广州塞到北京下面。前端联动根本产生不了这种组合。 */
    public function testACityFromAnotherProvinceIsRejected()
    {
        $result = $this->validate($this->envelope('110000', '440100', '440103'));

        $this->assertFalse($result->isValid());
        $this->assertStringContainsString('440100', $result->errorText());
        $this->assertSame([], $result->rows(), '拒绝时不得留下任何单元格');
    }

    /** 篡改二：编一个不存在的区代码。 */
    public function testADistrictThatDoesNotExistIsRejected()
    {
        $result = $this->validate($this->envelope('440000', '440100', '440199'));

        $this->assertFalse($result->isValid());
        $this->assertStringContainsString('440199', $result->errorText());
    }

    /** 篡改三：拿上一版字典里的节点来填这一版的题。 */
    public function testANodeFromAnotherVersionIsRejected()
    {
        $result = $this->validate($this->envelope('440000', '440100', '440104'));

        $this->assertFalse($result->isValid());
        $this->assertStringContainsString('440104', $result->errorText());
    }

    /** 篡改四：跳过一级，把区直接填在第二级。 */
    public function testSkippingALevelIsRejected()
    {
        $result = $this->validate($this->envelope('440000', '440103', '440103'));

        $this->assertFalse($result->isValid());
    }

    /** 篡改五：从市开始，不从根开始。 */
    public function testAPathThatDoesNotStartAtTheRootIsRejected()
    {
        $result = $this->validate($this->envelope('440100', '440103', '440103'));

        $this->assertFalse($result->isValid());
    }

    public function testAMissingLevelIsRejectedBecauseEveryLevelIsRequired()
    {
        $result = $this->validate($this->envelope('440000', '440100', ''));

        $this->assertFalse($result->isValid());
    }

    /** 没有字典就判不了路径，只能拒——不能因为查不到就放行。 */
    public function testWithoutADictionaryStoreTheAnswerIsRefusedRatherThanWavedThrough()
    {
        $result = $this->validate($this->envelope('440000', '440100', '440103'), null);

        $this->assertFalse($result->isValid());
        $this->assertStringContainsString('字典', $result->errorText());
    }

    /** 引擎上根本没装这一版时同理：宁可这道题谁也交不了，也不能收下判定不了的路径。 */
    public function testAnUninstalledVersionIsRefused()
    {
        $columns = str_replace('2024.1', '1999.1', self::COLUMNS);
        $validator = new \MjyRepeatingTableValidator(
            \MjyTableColumnSpec::fromJson($columns), 1, 1, self::$store);

        $result = $validator->validate($this->envelope('440000', '440100', '440103'));

        $this->assertFalse($result->isValid());
    }

    /** 未知列照旧被拒，不因为多了字典列就放松。 */
    public function testAnUnknownColumnIsStillRejected()
    {
        $answer = json_encode(['v' => 1, 'rows' => [[
            'L1' => '440000', 'L2' => '440100', 'L3' => '440103', 'label' => '我自己写的',
        ]]]);

        $this->assertFalse($this->validate($answer)->isValid());
    }

    /** 行数钉死成 1：一个人只在一个地方。 */
    public function testTwoRowsAreRejected()
    {
        $answer = json_encode(['v' => 1, 'rows' => [
            ['L1' => '440000', 'L2' => '440100', 'L3' => '440103'],
            ['L1' => '110000', 'L2' => '110100', 'L3' => '110101'],
        ]]);

        $this->assertFalse($this->validate($answer)->isValid());
    }

    /** 代码字符集不合法的值在取字典之前就该被挡下来，不该变成一次查询。 */
    public function testAnIllegalNodeCodeIsRejected()
    {
        $this->assertFalse($this->validate($this->envelope('440000', '440100', "'; DROP TABLE"))->isValid());
    }

    /**
     * 字典代码不必是数字。
     *
     * 行政区划的 GB/T 2260 代码碰巧全是数字，所以前面那一批用例撑不起这一条；
     * 而变异验证（把路径判定整段拿掉）恰好暴露了真问题：字典列原本会落进数值分支，
     * 于是门店、品类这类字母代码的字典会被当成“必须是数字”拒掉。
     */
    public function testAlphabeticDictionaryCodesAreAccepted()
    {
        $store = new \MjyDictionaryStore(\App()->getDb());
        $store->ensureSchema();
        $store->forget('store-directory', 'v1');
        $store->install('store-directory', 'v1', 'dg1:2222222222222222', [
            ['NORTH', '', '华北大区'],
            ['BJ01', 'NORTH', '北京一店'],
        ]);
        $columns = '[
            {"code":"L1","label":"大区","type":"dict","required":true,
             "dictionary":"store-directory","dictionaryVersion":"v1","level":1},
            {"code":"L2","label":"门店","type":"dict","required":true,
             "dictionary":"store-directory","dictionaryVersion":"v1","level":2}
        ]';
        $validator = new \MjyRepeatingTableValidator(
            \MjyTableColumnSpec::fromJson($columns), 1, 1, $store);

        $good = $validator->validate(json_encode(['v' => 1, 'rows' => [['L1' => 'NORTH', 'L2' => 'BJ01']]]));
        $crossed = $validator->validate(json_encode(['v' => 1, 'rows' => [['L1' => 'BJ01', 'L2' => 'NORTH']]]));

        $this->assertTrue($good->isValid(), implode('; ', $good->errors()));
        $this->assertFalse($crossed->isValid(), '路径反过来同样不成立');
        $store->forget('store-directory', 'v1');
    }
}
