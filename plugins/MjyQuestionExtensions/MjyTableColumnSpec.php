<?php

/**
 * 自增表格的列定义。
 *
 * 存在题目属性 mjy_table_columns 里，随 .lss 一起发布（question_attributes 小节）。
 * 引擎不认识这个属性，也不会校验它的内容，所以解析时必须自己把边界守住：
 * 结构不对就抛异常，宁可发布失败，也不要带着半个列字典上线。
 */
class MjyTableColumnSpec
{
    public const TYPE_TEXT = 'text';
    public const TYPE_INTEGER = 'integer';
    public const TYPE_DECIMAL = 'decimal';
    /** 取值集合由平台在发布时声明（R02-11 循环评价用它认评分与评价对象）。 */
    public const TYPE_ENUM = 'enum';

    private const TYPES = [self::TYPE_TEXT, self::TYPE_INTEGER, self::TYPE_DECIMAL, self::TYPE_ENUM];
    private const CODE_PATTERN = '/^[A-Za-z][A-Za-z0-9_]{0,31}$/';
    /** 取值代码比列代码宽一位：量表常写成 1…5（与网关 _VALUE_CODE_PATTERN 一致）。 */
    private const VALUE_PATTERN = '/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/';
    private const MAX_COLUMNS = 40;
    private const MAX_OPTIONS = 200;

    /** @var array<int, array<string, mixed>> */
    private $columns;

    /**
     * @param array<int, array<string, mixed>> $columns
     */
    private function __construct(array $columns)
    {
        $this->columns = $columns;
    }

    /**
     * @throws InvalidArgumentException 列定义不可用时
     */
    public static function fromJson(?string $json): self
    {
        $decoded = json_decode((string) $json, true);
        if (!is_array($decoded) || $decoded === [] || !self::isList($decoded)) {
            throw new InvalidArgumentException('mjy_table_columns 必须是非空的 JSON 数组');
        }
        if (count($decoded) > self::MAX_COLUMNS) {
            throw new InvalidArgumentException('mjy_table_columns 的列数超过上限 ' . self::MAX_COLUMNS);
        }

        $columns = [];
        $seenCodes = [];
        foreach ($decoded as $raw) {
            $column = self::parseColumn($raw);
            if (isset($seenCodes[$column['code']])) {
                throw new InvalidArgumentException('列代码重复：' . $column['code']);
            }
            $seenCodes[$column['code']] = true;
            $columns[] = $column;
        }
        return new self($columns);
    }

    /**
     * @return array<int, array<string, mixed>>
     */
    public function columns(): array
    {
        return $this->columns;
    }

    /**
     * @return string[] 列代码，顺序即归一化后的列顺序
     */
    public function codes(): array
    {
        return array_column($this->columns, 'code');
    }

    /**
     * @param array<string, mixed>|mixed $raw
     * @return array<string, mixed>
     */
    private static function parseColumn($raw): array
    {
        if (!is_array($raw) || !isset($raw['code']) || !is_string($raw['code'])) {
            throw new InvalidArgumentException('每一列都必须有字符串型的 code');
        }
        $code = $raw['code'];
        if (preg_match(self::CODE_PATTERN, $code) !== 1) {
            throw new InvalidArgumentException('列代码不合法：' . $code);
        }
        $type = $raw['type'] ?? self::TYPE_TEXT;
        if (!in_array($type, self::TYPES, true)) {
            throw new InvalidArgumentException('列类型不支持：' . var_export($type, true));
        }
        return [
            'code' => $code,
            'label' => isset($raw['label']) && is_scalar($raw['label']) ? (string) $raw['label'] : $code,
            'type' => $type,
            'required' => !empty($raw['required']),
            'maxLength' => isset($raw['maxLength']) ? (int) $raw['maxLength'] : null,
            'min' => isset($raw['min']) ? (float) $raw['min'] : null,
            'max' => isset($raw['max']) ? (float) $raw['max'] : null,
            'options' => $type === self::TYPE_ENUM ? self::parseOptions($raw['options'] ?? null, $code) : null,
            // 唯一列：同一列的非空取值在一次作答里不得重复。
            'distinct' => !empty($raw['distinct']),
        ];
    }

    /**
     * 枚举列的取值代码。写成 ["A","B"] 或 [{"code":"A","label":"优"}] 都认，
     * 这里只留代码——标签是渲染的事，校验只看代码。
     *
     * @param mixed $raw
     * @return string[]
     */
    private static function parseOptions($raw, string $code): array
    {
        if (!is_array($raw) || $raw === [] || !self::isList($raw)) {
            throw new InvalidArgumentException('enum 列必须声明非空的 options：' . $code);
        }
        if (count($raw) > self::MAX_OPTIONS) {
            throw new InvalidArgumentException('enum 列的取值超过上限 ' . self::MAX_OPTIONS . '：' . $code);
        }
        $options = [];
        foreach ($raw as $entry) {
            $value = is_array($entry) ? ($entry['code'] ?? null) : $entry;
            if (!is_string($value) || preg_match(self::VALUE_PATTERN, $value) !== 1) {
                throw new InvalidArgumentException('enum 列的取值代码不合法：' . $code);
            }
            if (in_array($value, $options, true)) {
                throw new InvalidArgumentException('enum 列的取值代码重复：' . $code);
            }
            $options[] = $value;
        }
        return $options;
    }

    /**
     * @param array<mixed> $value
     */
    private static function isList(array $value): bool
    {
        return array_keys($value) === range(0, count($value) - 1);
    }
}
