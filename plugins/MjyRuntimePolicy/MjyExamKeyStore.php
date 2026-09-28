<?php

/**
 * 读平台下发的考试答案键（契约 survey-exam-v1 §4）。
 *
 * 与 MjyAccessPolicyStore 同一条载体、同一套失败语义：
 * 恰好一行才算数，多于一行说明被人手工动过，无法判断哪份算数——判分场景下
 * 这必须是错误而不是"挑一份用"。
 */
class MjyExamKeyStore
{
    public const EXAM_KEY = 'mjy_exam_key';

    /** @var MjySurveySettingReader */
    private $reader;

    public function __construct(CDbConnection $db, int $pluginId)
    {
        $this->reader = new MjySurveySettingReader($db, $pluginId);
    }

    /**
     * @return MjyExamKey|null 没有答案键返回 null（这份问卷不是考试）
     * @throws RuntimeException 多于一份
     * @throws InvalidArgumentException 解析失败
     */
    public function find(int $surveyId): ?MjyExamKey
    {
        $values = $this->reader->values($surveyId, self::EXAM_KEY);
        if ($values === []) {
            return null;
        }
        if (count($values) > 1) {
            throw new RuntimeException("问卷 {$surveyId} 的考试答案键有 " . count($values) . ' 份');
        }
        return MjyExamKey::fromJson($values[0]);
    }

    /**
     * 给发布网关回读用。
     *
     * **只报份数、能否解析、摘要，绝不回答案内容**——这个端点没有鉴权
     * （newDirectRequest 是公开路由），把答案回出去等于换个地方下发。
     * 摘要是 SHA-256：网关拿自己编译出来的那份算一遍比对，对不上就回滚发布。
     *
     * @return array{surveyId: int, rows: int, valid: bool, examDigest: string|null, questions: int|null}
     */
    public function status(int $surveyId): array
    {
        $values = $this->reader->values($surveyId, self::EXAM_KEY);
        $isValid = false;
        $digest = null;
        $questions = null;
        if (count($values) === 1) {
            $digest = hash('sha256', $values[0]);
            try {
                // 题数不是秘密（作答者数得出来），但能让平台确认"下发的那份确实被认了"。
                $questions = count(MjyExamKey::fromJson($values[0])->questionCodes());
                $isValid = true;
            } catch (InvalidArgumentException $exception) {
                $isValid = false;
            }
        }
        return [
            'surveyId' => $surveyId,
            'rows' => count($values),
            'valid' => $isValid,
            'examDigest' => $digest,
            'questions' => $questions,
        ];
    }
}
