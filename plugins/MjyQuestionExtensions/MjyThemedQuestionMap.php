<?php

/**
 * 一份问卷里与平台扩展题型相关的题目。
 *
 * 键一律用**题目代码**（`lime_questions.title`）：LimeSurvey 7 的答卷列名是
 * `Q<qid>`，只由数字主键决定，跨实例、跨导入批次都会变（[ADR 0005] 场景二）。
 * 字段名只作为本次激活期内的临时投影，每次都现算，不缓存到副表里。
 */
class MjyThemedQuestionMap
{
    /** 自增表格的题型主题名，必须与 themes/question/ 下的目录名一致。 */
    public const REPEATING_TABLE_THEME = 'mjy-repeating-table';

    /** @var array<string, array<string, mixed>> */
    private $repeatingTables;

    /** @var array<string, array<string, mixed>> */
    private $uploads;

    /**
     * @param array<string, array<string, mixed>> $repeatingTables
     * @param array<string, array<string, mixed>> $uploads
     */
    private function __construct(array $repeatingTables, array $uploads)
    {
        $this->repeatingTables = $repeatingTables;
        $this->uploads = $uploads;
    }

    public static function forSurvey(CDbConnection $db, int $surveyId): self
    {
        $rows = $db->createCommand()
            ->select('qid, title, type, question_theme_name')
            ->from($db->tablePrefix . 'questions')
            ->where('sid = :sid AND parent_qid = 0', [':sid' => $surveyId])
            ->order('qid')
            ->queryAll();

        $repeatingTables = [];
        $uploads = [];
        foreach ($rows as $row) {
            $entry = [
                'qid' => (int) $row['qid'],
                'code' => (string) $row['title'],
                'fieldName' => 'Q' . (int) $row['qid'],
            ];
            if ((string) $row['question_theme_name'] === self::REPEATING_TABLE_THEME) {
                $repeatingTables[$entry['code']] = $entry;
                continue;
            }
            if ((string) $row['type'] === Question::QT_VERTICAL_FILE_UPLOAD) {
                $uploads[$entry['code']] = $entry;
            }
        }
        return new self($repeatingTables, $uploads);
    }

    /**
     * @return array<string, array<string, mixed>> 题目代码 => 题目信息
     */
    public function repeatingTables(): array
    {
        return $this->repeatingTables;
    }

    /**
     * @return array<string, array<string, mixed>> 题目代码 => 题目信息
     */
    public function uploads(): array
    {
        return $this->uploads;
    }

    /**
     * @return string|null 该字段名对应的上传题代码
     */
    public function uploadCodeForQuestionId(int $qid): ?string
    {
        foreach ($this->uploads as $code => $upload) {
            if ($upload['qid'] === $qid) {
                return $code;
            }
        }
        return null;
    }
}
