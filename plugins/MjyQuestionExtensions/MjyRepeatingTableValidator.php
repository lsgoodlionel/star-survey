<?php

/**
 * 自增表格作答的服务端校验与归一化。
 *
 * 引擎把整张表当成一个长文本字段存，题型主题的 JS 只负责编辑体验；
 * 提交上来的 JSON 一律在这里重新解析，客户端的任何结论都不被信任。
 */
class MjyRepeatingTableValidator
{
    public const ENVELOPE_VERSION = 1;

    /** 题目没配上限时的兜底行数：**失败关闭**，不是「不限行」。 */
    public const DEFAULT_MAX_ROWS = 20;

    /** 代码层面的绝对上限，题目属性再大也不能越过。 */
    public const HARD_MAX_ROWS = 500;

    /** 解码之前就丢弃的报文长度上限（字节）。 */
    public const MAX_PAYLOAD_LENGTH = 262144;

    /** 单元格的绝对上限，防止一次提交撑爆副表。 */
    private const CELL_MAX_LENGTH = 2000;

    /** 未知列名完全由作答者决定，回显前只保留这些字符。 */
    private const SAFE_LABEL_PATTERN = '/[^A-Za-z0-9_\-]/';

    private const SAFE_LABEL_MAX_LENGTH = 32;

    /** @var MjyTableColumnSpec */
    private $spec;

    /** @var int */
    private $minRows;

    /** @var int */
    private $maxRows;

    public function __construct(MjyTableColumnSpec $spec, int $minRows, int $maxRows)
    {
        $this->spec = $spec;
        $this->minRows = max(0, $minRows);
        // 属性缺失或为 0 时回落到默认上限，绝不放开成无限行。
        $this->maxRows = min($maxRows > 0 ? $maxRows : self::DEFAULT_MAX_ROWS, self::HARD_MAX_ROWS);
    }

    public function validate(?string $rawAnswer): MjyValidationResult
    {
        $answer = trim((string) $rawAnswer);
        if (strlen($answer) > self::MAX_PAYLOAD_LENGTH) {
            // 先按字节长度挡掉，避免把超大报文 json_decode 进内存。
            return MjyValidationResult::invalid(['作答内容超过长度上限']);
        }
        if ($answer === '') {
            return $this->minRows > 0
                ? MjyValidationResult::invalid([sprintf('至少需要填写 %d 行', $this->minRows)])
                : MjyValidationResult::valid([]);
        }

        $envelopeRows = $this->decodeEnvelope($answer);
        if (!is_array($envelopeRows)) {
            return MjyValidationResult::invalid([(string) $envelopeRows]);
        }

        $countErrors = $this->checkRowCount(count($envelopeRows));
        if ($countErrors !== []) {
            return MjyValidationResult::invalid($countErrors);
        }

        $rows = [];
        $errors = [];
        foreach ($envelopeRows as $index => $rawRow) {
            $row = $this->normaliseRow($rawRow, $index + 1, $errors);
            if ($row !== null) {
                $rows[] = $row;
            }
        }
        return $errors === [] ? MjyValidationResult::valid($rows) : MjyValidationResult::invalid($errors);
    }

    /**
     * 刻意不用 json_decode 的关联数组模式：那样 {"0":{...}} 和 [{...}] 在 PHP 里
     * 长得一模一样，JSON 对象会被当成数组放行。
     *
     * @return array<int, mixed>|string 行数组，或一条错误说明
     */
    private function decodeEnvelope(string $answer)
    {
        $decoded = json_decode($answer);
        if (!$decoded instanceof stdClass) {
            return '作答内容不是合法的 JSON 对象';
        }
        if (($decoded->v ?? null) !== self::ENVELOPE_VERSION) {
            return '作答信封版本不是 ' . self::ENVELOPE_VERSION;
        }
        if (!isset($decoded->rows) || !is_array($decoded->rows)) {
            return 'rows 必须是数组';
        }
        return $decoded->rows;
    }

    /**
     * @return string[]
     */
    private function checkRowCount(int $count): array
    {
        if ($count < $this->minRows) {
            return [sprintf('至少需要填写 %d 行，实际 %d 行', $this->minRows, $count)];
        }
        if ($count > $this->maxRows) {
            return [sprintf('最多只能填写 %d 行，实际 %d 行', $this->maxRows, $count)];
        }
        return [];
    }

    /**
     * @param mixed $rawRow
     * @param string[] $errors 按引用累加
     * @return array<string, string>|null
     */
    private function normaliseRow($rawRow, int $rowNumber, array &$errors): ?array
    {
        if (!$rawRow instanceof stdClass) {
            $errors[] = sprintf('第 %d 行不是对象', $rowNumber);
            return null;
        }
        $cells = get_object_vars($rawRow);
        $unknown = array_diff(array_keys($cells), $this->spec->codes());
        if ($unknown !== []) {
            // 悄悄丢弃未知列会让平台与引擎的列字典无声漂移。
            // 列名来自作答者，而错误文案会被引擎以 raw 渲染
            // （themes/survey/*/views/subviews/survey/question_subviews/valid_message_and_help.twig），
            // 所以在源头就把非法字符去掉，不依赖下游转义。
            $errors[] = sprintf(
                '第 %d 行包含未知列：%s',
                $rowNumber,
                implode(',', array_map([self::class, 'safeLabel'], $unknown))
            );
            return null;
        }

        $row = [];
        foreach ($this->spec->columns() as $column) {
            $value = $this->scalarValue($cells[$column['code']] ?? '', $column, $rowNumber, $errors);
            if ($value === null) {
                return null;
            }
            $this->checkColumn($value, $column, $rowNumber, $errors);
            $row[$column['code']] = $value;
        }
        return $row;
    }

    /**
     * @param mixed $raw
     * @param array<string, mixed> $column
     * @param string[] $errors
     */
    private function scalarValue($raw, array $column, int $rowNumber, array &$errors): ?string
    {
        if (is_array($raw) || is_object($raw)) {
            $errors[] = sprintf('第 %d 行的 %s 不是标量', $rowNumber, $column['label']);
            return null;
        }
        if (is_bool($raw)) {
            $errors[] = sprintf('第 %d 行的 %s 不能是布尔值', $rowNumber, $column['label']);
            return null;
        }
        return trim((string) $raw);
    }

    /**
     * @param array<string, mixed> $column
     * @param string[] $errors
     */
    private function checkColumn(string $value, array $column, int $rowNumber, array &$errors): void
    {
        $label = sprintf('第 %d 行的 %s', $rowNumber, $column['label']);
        if ($value === '') {
            if ($column['required']) {
                $errors[] = $label . ' 不能为空';
            }
            return;
        }
        $length = mb_strlen($value, 'UTF-8');
        if ($length > self::CELL_MAX_LENGTH) {
            $errors[] = $label . ' 超过单元格长度上限';
            return;
        }
        if ($column['maxLength'] !== null && $length > $column['maxLength']) {
            $errors[] = sprintf('%s 超过 %d 个字符', $label, $column['maxLength']);
            return;
        }
        if ($column['type'] === MjyTableColumnSpec::TYPE_TEXT) {
            return;
        }
        $this->checkNumber($value, $column, $label, $errors);
    }

    /**
     * @param array<string, mixed> $column
     * @param string[] $errors
     */
    private function checkNumber(string $value, array $column, string $label, array &$errors): void
    {
        $isInteger = $column['type'] === MjyTableColumnSpec::TYPE_INTEGER;
        if ($isInteger && preg_match('/^-?\d+$/', $value) !== 1) {
            $errors[] = $label . ' 必须是整数';
            return;
        }
        if (!$isInteger && !is_numeric($value)) {
            $errors[] = $label . ' 必须是数字';
            return;
        }
        $number = (float) $value;
        if ($column['min'] !== null && $number < $column['min']) {
            $errors[] = sprintf('%s 不能小于 %s', $label, $this->formatBound($column['min']));
            return;
        }
        if ($column['max'] !== null && $number > $column['max']) {
            $errors[] = sprintf('%s 不能大于 %s', $label, $this->formatBound($column['max']));
        }
    }

    /**
     * 把作答者提供的列名压成一个只含字母数字下划线连字符的短标识。
     *
     * @param string $raw
     */
    private static function safeLabel($raw): string
    {
        $safe = preg_replace(self::SAFE_LABEL_PATTERN, '', (string) $raw);
        $safe = mb_substr((string) $safe, 0, self::SAFE_LABEL_MAX_LENGTH, 'UTF-8');
        return $safe === '' ? '?' : $safe;
    }

    private function formatBound(float $bound): string
    {
        return rtrim(rtrim(sprintf('%.4F', $bound), '0'), '.');
    }
}
