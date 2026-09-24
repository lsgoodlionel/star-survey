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

    /** @var MjyDictionaryStore|null 有 dict 列时必须给；给不出来就拒收（失败关闭）。 */
    private $dictionaries;

    public function __construct(
        MjyTableColumnSpec $spec,
        int $minRows,
        int $maxRows,
        ?MjyDictionaryStore $dictionaries = null
    ) {
        $this->spec = $spec;
        $this->minRows = max(0, $minRows);
        // 属性缺失或为 0 时回落到默认上限，绝不放开成无限行。
        $this->maxRows = min($maxRows > 0 ? $maxRows : self::DEFAULT_MAX_ROWS, self::HARD_MAX_ROWS);
        $this->dictionaries = $dictionaries;
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
        if ($errors === []) {
            $errors = $this->checkDistinct($rows);
        }
        if ($errors === []) {
            $errors = $this->checkDictionaryPaths($rows);
        }
        return $errors === [] ? MjyValidationResult::valid($rows) : MjyValidationResult::invalid($errors);
    }

    /**
     * 唯一列：同一列的**非空**取值在一次作答里不得重复。
     *
     * 循环评价（R02-11）靠它把「每个评价对象恰好评一次」钉住：对象列是枚举，
     * 行数被 min/max 钉死成对象个数，再加上这一条唯一约束，三者合起来就是一一对应。
     * 空值不算重复——非必答的唯一列可以有多行留空。
     *
     * @param array<int, array<string, string>> $rows
     * @return string[]
     */
    private function checkDistinct(array $rows): array
    {
        $errors = [];
        foreach ($this->spec->columns() as $column) {
            if (!$column['distinct']) {
                continue;
            }
            $seen = [];
            foreach ($rows as $index => $row) {
                $value = $row[$column['code']] ?? '';
                if ($value === '') {
                    continue;
                }
                if (isset($seen[$value])) {
                    $errors[] = sprintf(
                        '第 %d 行的 %s 与第 %d 行重复',
                        $index + 1,
                        $column['label'],
                        $seen[$value]
                    );
                    continue;
                }
                $seen[$value] = $index + 1;
            }
        }
        return $errors;
    }

    /**
     * 多级下拉（R02-03）的**路径判定**：同一本字典的几个 dict 列按 level 排好，
     * 构成一条自上而下的路径；这条路径必须在**那一版**字典里真实存在、逐级相连。
     *
     * 为什么不能靠逐单元格判：“440100 是不是一个合法的市”单看一格是成立的，
     * 但“它在北京下面”不成立。跨省的市、跨版本的节点都只有把整条路径放在一起看才拦得住。
     *
     * **没有字典就拒**：查不到不等于合法。此时这道题谁也交不了，那正是应有的结果——
     * 发布期的对账（网关 E_DICTIONARY_MISSING）本就不应该让这种问卷发得出去。
     *
     * @param array<int, array<string, string>> $rows
     * @return string[]
     */
    private function checkDictionaryPaths(array $rows): array
    {
        $groups = $this->dictionaryGroups();
        if ($groups === []) {
            return [];
        }
        if ($this->dictionaries === null) {
            return ['本题要按平台字典判定所选层级，但引擎上没有可用的字典'];
        }
        $errors = [];
        foreach ($groups as $group) {
            foreach ($rows as $index => $row) {
                $errors = array_merge($errors, $this->checkOnePath($group, $row, $index + 1));
            }
        }
        return $errors;
    }

    /**
     * 按 (字典, 版本) 分组的 dict 列，组内按 level 升序。
     * 一道题里允许有多组（例如同时问户籍地与现居地）。
     *
     * @return array<string, array<int, array<string, mixed>>>
     */
    private function dictionaryGroups(): array
    {
        $groups = [];
        foreach ($this->spec->columns() as $column) {
            if ($column['type'] !== MjyTableColumnSpec::TYPE_DICT) {
                continue;
            }
            $groups[$column['dictionary'] . '@' . $column['dictionaryVersion']][] = $column;
        }
        foreach ($groups as &$columns) {
            usort($columns, static function (array $left, array $right): int {
                return $left['level'] <=> $right['level'];
            });
        }
        unset($columns);
        return $groups;
    }

    /**
     * @param array<int, array<string, mixed>> $columns 同一本字典同一版的列，已按 level 升序
     * @param array<string, string> $row
     * @return string[]
     */
    private function checkOnePath(array $columns, array $row, int $rowNumber): array
    {
        $codes = [];
        foreach ($columns as $column) {
            $value = $row[$column['code']] ?? '';
            if ($value === '') {
                // 必填已由 checkColumn 管住；非必填的空值意味着这条路径到此为止。
                break;
            }
            $codes[] = $value;
        }
        if ($codes === []) {
            return [];
        }
        $first = $columns[0];
        $found = $this->dictionaries->nodesIn($first['dictionary'], $first['dictionaryVersion'], $codes);
        $parent = '';
        foreach ($codes as $position => $code) {
            $label = sprintf('第 %d 行的 %s', $rowNumber, $columns[$position]['label']);
            if (!isset($found[$code])) {
                return [$label . ' 的 ' . $code . ' 不在本题所用的那一版字典里'];
            }
            $node = $found[$code];
            if ((int) $node['depth'] !== $columns[$position]['level']) {
                return [$label . ' 的 ' . $code . ' 不是这一层的取值'];
            }
            if ((string) $node['parent_code'] !== $parent) {
                return [$label . ' 的 ' . $code . ' 不属于上一级'];
            }
            $parent = $code;
        }
        return [];
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
        if ($column['type'] === MjyTableColumnSpec::TYPE_ENUM) {
            // 取值集合由平台声明，作答者只能从里面挑；浏览器端渲染成什么都不作数。
            if (!in_array($value, $column['options'] ?? [], true)) {
                $errors[] = $label . ' 不在可选范围内';
            }
            return;
        }
        if ($column['type'] === MjyTableColumnSpec::TYPE_DICT) {
            // 字典列只在这里看字符集：“这个代码存不存在”要把整条路径放在一起看（checkDictionaryPaths）。
            // 这一步不是多余的：它把能当参数用的值拦在查库之前，并且不让字典列落进数值分支——
            // 行政区划代码碰巧全是数字，别的字典（门店、品类）并不是。
            if (preg_match(MjyTableColumnSpec::VALUE_CODE_PATTERN, $value) !== 1) {
                $errors[] = $label . ' 不是合法的字典代码';
            }
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
