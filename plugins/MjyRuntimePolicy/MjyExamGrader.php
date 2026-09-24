<?php

/**
 * 客观题自动评分（WP-10）。纯函数：没有数据库、没有会话、没有时钟。
 *
 * 两个输入：平台下发的答案键（只存在于 plugin_settings 里，WP-09.1），
 * 与作答者写进答卷表的答案。**对错是在这里推出来的，不是作答者报上来的**——
 * 答卷表里根本没有可以放对错的列（与 mjy-psych-trial 的列定义同一条原则：
 * 信封里没有能放"我答对了"的位置）。
 *
 * 判分口径刻意保守：
 * - 多选是整套对才给分。部分给分不是"更宽容"，是另一种计分模型，不同考试要的
 *   口径不一样（按选对个数、按对减错、设下限……），没有需求就不猜（YAGNI）。
 * - 选项代码精确匹配，不折叠大小写：代码是平台生成的，不是人打出来的。
 * - 文本与数值按答案键里声明的口径匹配；数值按数值比，"3.0" 与 "3" 是同一个数。
 *
 * 答卷表的内容归根到底是作答者写的，所以任何形状的值都不能把判分搞崩：
 * 类型不对一律当作没答对，不抛异常。
 */
class MjyExamGrader
{
    /**
     * @param array<string, mixed> $answers 题目代码 → 作答者的答案
     *                                      （选择题是代码，多选是代码数组，其余是字符串）
     */
    public function grade(MjyExamKey $key, array $answers): MjyExamResult
    {
        $score = 0.0;
        $maxScore = 0.0;
        $detail = [];
        foreach ($key->entries() as $entry) {
            $given = $answers[$entry->code()] ?? null;
            $isCorrect = $this->isCorrect($entry, $given);
            $awarded = $isCorrect ? $entry->points() : 0.0;
            $score += $awarded;
            $maxScore += $entry->points();
            $detail[$entry->code()] = [
                'correct' => $isCorrect,
                'points' => $awarded,
                'answered' => $this->isAnswered($given),
            ];
        }
        return new MjyExamResult($score, $maxScore, $detail);
    }

    /**
     * @param mixed $given
     */
    private function isCorrect(MjyExamKeyEntry $entry, $given): bool
    {
        switch ($entry->kind()) {
            case MjyExamKeyEntry::KIND_CHOICE:
                return is_string($given) && $given !== '' && in_array($given, $entry->correct(), true);
            case MjyExamKeyEntry::KIND_SET:
                return $this->setMatches($entry, $given);
            case MjyExamKeyEntry::KIND_NUMBER:
                return $this->numberMatches($entry, $given);
            default:
                return $this->textMatches($entry, $given);
        }
    }

    /**
     * 多选：整套相同才算对，顺序无关，多选少选都不给分。
     *
     * @param mixed $given
     */
    private function setMatches(MjyExamKeyEntry $entry, $given): bool
    {
        if (!is_array($given)) {
            return false;
        }
        $chosen = array_values(array_unique(array_filter($given, 'is_string')));
        if (count($chosen) !== count($given)) {
            return false; // 有非字符串或重复项：来路不正，不给分
        }
        sort($chosen);
        $wanted = $entry->correct();
        sort($wanted);
        return $chosen === $wanted;
    }

    /**
     * @param mixed $given
     */
    private function numberMatches(MjyExamKeyEntry $entry, $given): bool
    {
        if (!is_string($given) && !is_int($given) && !is_float($given)) {
            return false;
        }
        $candidate = trim((string) $given);
        if ($candidate === '' || !is_numeric($candidate)) {
            return false;
        }
        foreach ($entry->correct() as $answer) {
            if (is_numeric($answer) && (float) $answer === (float) $candidate) {
                return true;
            }
        }
        return false;
    }

    /**
     * @param mixed $given
     */
    private function textMatches(MjyExamKeyEntry $entry, $given): bool
    {
        if (!is_string($given)) {
            return false;
        }
        $candidate = $entry->trim() ? trim($given) : $given;
        if ($candidate === '') {
            return false;
        }
        foreach ($entry->correct() as $answer) {
            $wanted = $entry->trim() ? trim($answer) : $answer;
            if ($candidate === $wanted) {
                return true;
            }
            // 大小写折叠用 mb_strtolower：答案里可能有非 ASCII。
            if ($entry->ignoreCase() && mb_strtolower($candidate) === mb_strtolower($wanted)) {
                return true;
            }
        }
        return false;
    }

    /**
     * @param mixed $given
     */
    private function isAnswered($given): bool
    {
        if (is_array($given)) {
            return $given !== [];
        }
        return is_string($given) ? trim($given) !== '' : $given !== null;
    }
}
