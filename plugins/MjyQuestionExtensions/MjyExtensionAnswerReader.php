<?php

/**
 * 按页批量读扩展表作答（ADR 0018 决定 10，契约 plugin-channel-v1）。
 *
 * MjyStructuredAnswerStore 的 fetchRows()/fetchState() 是「一条答卷的一道题」的访问器，
 * 是校验与投影路径的接口。按它们做分页读会是 N+1：200 条答卷 × 若干扩展题 = 数百次查询。
 * 所以这里用两条查询把整页取回来，单答卷访问器保持不动。
 *
 * 两条查询各自的占位符只出现一次（PostgreSQL 原生预处理不允许重复，同 ADR 0003 决定 6）。
 *
 * 单元格超出上限时返回 null，由调用方拒绝整页——**绝不静默截断**：
 * 导出少几行而没有任何人察觉，比报错危险得多。
 */
class MjyExtensionAnswerReader
{
    /** @var CDbConnection */
    private $db;

    /** @var MjyStructuredAnswerStore */
    private $store;

    public function __construct(CDbConnection $db, MjyStructuredAnswerStore $store)
    {
        $this->db = $db;
        $this->store = $store;
    }

    /**
     * @param int[]    $responseIds   已校验的答卷号（升序、互不相同、有上限）
     * @param string[] $questionCodes 已校验的题目代码
     * @param int      $cellLimit     本页允许的单元格数上限
     * @return array<int, array<string, array{structureVersion: string, isValid: bool, rows: array}>>|null
     *         null ＝ 超出单元格上限
     */
    public function read(
        int $surveyId,
        string $generation,
        array $responseIds,
        array $questionCodes,
        int $cellLimit
    ): ?array {
        $bind = $this->binding($surveyId, $generation, $responseIds, $questionCodes);
        $cells = $this->db->createCommand()
            ->select('response_id, question_code, row_index, column_code, cell_value, structure_version')
            ->from($this->store->tableName())
            ->where($bind['condition'], $bind['params'])
            // 多取一行用来探出溢出：正好等于上限时不算超。
            ->order('response_id, question_code, row_index, id')
            ->limit($cellLimit + 1)
            ->queryAll();
        if (count($cells) > $cellLimit) {
            return null;
        }
        $states = $this->db->createCommand()
            ->select('response_id, question_code, structure_version, is_valid')
            ->from($this->store->stateTableName())
            ->where($bind['condition'], $bind['params'])
            ->queryAll();

        return $this->assemble($cells, $states);
    }

    /**
     * 每个占位符只出现一次。
     *
     * @param int[]    $responseIds
     * @param string[] $questionCodes
     * @return array{condition: string, params: array<string, mixed>}
     */
    private function binding(int $surveyId, string $generation, array $responseIds, array $questionCodes): array
    {
        $params = [
            ':instance' => $this->store->engineInstanceId(),
            ':sid' => $surveyId,
            ':gen' => $generation,
        ];
        $idNames = [];
        foreach (array_values($responseIds) as $index => $responseId) {
            $name = ':r' . $index;
            $idNames[] = $name;
            $params[$name] = (int) $responseId;
        }
        $codeNames = [];
        foreach (array_values($questionCodes) as $index => $questionCode) {
            $name = ':q' . $index;
            $codeNames[] = $name;
            $params[$name] = (string) $questionCode;
        }
        $condition = 'engine_instance_id = :instance AND survey_id = :sid AND generation = :gen'
            . ' AND response_id IN (' . implode(',', $idNames) . ')'
            . ' AND question_code IN (' . implode(',', $codeNames) . ')';

        return ['condition' => $condition, 'params' => $params];
    }

    /**
     * 单元格与状态合成应答。
     *
     * 元数据以 answer_state 为准。只有单元格、没有状态行的组合仍然收录，
     * 结构版本取自单元格，isValid 记为 true——校验不通过的作答写入的行数是 0
     * （MjyStructuredAnswerStore::writeState），所以「有单元格」本身就意味着当时通过了闸门。
     * 宁可这样推断，也不能把有数据的答卷悄悄丢掉。
     *
     * @param array<int, array<string, mixed>> $cells
     * @param array<int, array<string, mixed>> $states
     * @return array<int, array<string, array{structureVersion: string, isValid: bool, rows: array}>>
     */
    private function assemble(array $cells, array $states): array
    {
        $grouped = [];
        foreach ($cells as $cell) {
            $responseId = (int) $cell['response_id'];
            $questionCode = (string) $cell['question_code'];
            $rowIndex = (int) $cell['row_index'];
            $grouped[$responseId][$questionCode]['rows'][$rowIndex][(string) $cell['column_code']] =
                (string) $cell['cell_value'];
            $grouped[$responseId][$questionCode]['structureVersion'] = (string) $cell['structure_version'];
        }
        foreach ($states as $state) {
            $responseId = (int) $state['response_id'];
            $questionCode = (string) $state['question_code'];
            $grouped[$responseId][$questionCode]['structureVersion'] = (string) $state['structure_version'];
            $grouped[$responseId][$questionCode]['isValid'] = ((int) $state['is_valid']) === 1;
        }

        $answers = [];
        foreach ($grouped as $responseId => $questions) {
            foreach ($questions as $questionCode => $entry) {
                $answers[$responseId][$questionCode] = [
                    'structureVersion' => $entry['structureVersion'] ?? MjyStructuredAnswerStore::LEGACY_STRUCTURE_VERSION,
                    'isValid' => $entry['isValid'] ?? true,
                    'rows' => self::orderRows($entry['rows'] ?? []),
                ];
            }
        }
        ksort($answers);

        return $answers;
    }

    /**
     * 行序号收紧成连续下标（与 fetchRows() 的既有语义一致：下标即行序）。
     *
     * @param array<int, array<string, string>> $rows
     * @return array<int, array<string, string>>
     */
    private static function orderRows(array $rows): array
    {
        ksort($rows);
        return array_values($rows);
    }
}
