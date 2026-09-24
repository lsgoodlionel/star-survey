<?php

/**
 * 答卷行 → 按题目代码取答案（WP-10）。
 *
 * 引擎的答卷表按字段名存（`<sid>X<gid>X<qid>`，多选再带子题代码），答案键按
 * **题目代码**说话。换算靠引擎自己的 createFieldMap：字段名会随实例、导入、
 * 停用重启而变，题目代码不会（与契约 question-extension-tables-v1 里
 * "键是题目代码而不是字段名"同一个理由，common_helper.php:1759）。
 *
 * 字段映射由调用方注入，所以这个类可以脱离引擎单测。
 */
class MjyExamAnswerReader
{
    /** 多选题里表示"选中"的值。 */
    private const SELECTED = 'Y';

    /** @var array<string, array<string, mixed>> 字段名 → createFieldMap 条目 */
    private $fieldMap;

    /**
     * @param array<string, array<string, mixed>> $fieldMap createFieldMap($survey, 'full') 的结果
     */
    public function __construct(array $fieldMap)
    {
        $this->fieldMap = $fieldMap;
    }

    /**
     * @param array<string, mixed> $row   答卷表的一行
     * @param string[]             $codes 要读的题目代码（答案键点名的那几道）
     * @return array<string, mixed> 题目代码 → 答案（多选是子题代码数组，其余是字符串）
     */
    public function read(array $row, array $codes): array
    {
        $byCode = $this->columnsByQuestionCode();
        $answers = [];
        foreach ($codes as $code) {
            if (!isset($byCode[$code])) {
                continue; // 字段映射里没有这道题（被删了、或代码对不上）
            }
            $answers[$code] = $this->valueOf($byCode[$code], $row);
        }
        return $answers;
    }

    /**
     * 题目代码 → 它占的那些列。多选题占多列，每列一个子题。
     *
     * @return array<string, array<string, string>> 代码 → (字段名 → 子题代码，单列题的子题代码为 '')
     */
    private function columnsByQuestionCode(): array
    {
        $byCode = [];
        foreach ($this->fieldMap as $fieldName => $field) {
            // 固有列（id、submitdate、seed…）的 title 是空的，不属于任何一道题。
            $code = (string) ($field['title'] ?? '');
            if ($code === '') {
                continue;
            }
            $byCode[$code][(string) ($field['fieldname'] ?? $fieldName)] = (string) ($field['aid'] ?? '');
        }
        return $byCode;
    }

    /**
     * @param array<string, string> $columns 字段名 → 子题代码
     * @param array<string, mixed>  $row
     * @return string|string[]
     */
    private function valueOf(array $columns, array $row)
    {
        $subCodes = array_filter($columns, static fn(string $aid): bool => $aid !== '');
        if ($subCodes === []) {
            $fieldName = (string) array_key_first($columns);
            return isset($row[$fieldName]) ? (string) $row[$fieldName] : '';
        }
        // 多选：选中的是值为 Y 的那几列，读出来是子题代码的集合。
        $chosen = [];
        foreach ($subCodes as $fieldName => $aid) {
            if (isset($row[$fieldName]) && (string) $row[$fieldName] === self::SELECTED) {
                $chosen[] = $aid;
            }
        }
        return $chosen;
    }
}
