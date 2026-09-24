<?php

/**
 * 监考（WP-09.2）：把判定结果接到考场记录、强制交卷与剩余时间上。
 *
 * 判定本身仍在 MjyAccessGate。这里只做判定之后的三件事：
 *
 * 1. **记账**——每次进场／翻页记一笔考场记录（截止时刻写一次就不再动）；
 * 2. **到点强制交卷**——判定是"时间已到"时，先把已经落库的答案标成交卷，再让
 *    调用方去拒绝。顺序不能反：拒绝路径末尾是 `App()->end()`，先拒绝的话这份卷
 *    永远交不上去，作答者白考一场（ADR 0007 的遗留项）；
 * 3. **剩余时间**——由服务端算（MjyExamTimer），给页面做展示用。
 *
 * 没有限时的问卷完全不经过这里：`deadlineAt()` 为空就直接返回 null，
 * 不建表记录、不产生任何开销。
 */
class MjyExamProctor
{
    /** @var MjyExamAttemptStore */
    private $attempts;

    /** @var callable|null 判分回调：(问卷号, 答卷行号) → void。没有答案键就不设。 */
    private $grade;

    public function __construct(CDbConnection $db, string $engineInstanceId)
    {
        $this->attempts = new MjyExamAttemptStore($db, $engineInstanceId);
    }

    /**
     * 挂上判分（WP-10）。强制交卷与正常交卷都要判，所以由监考统一触发：
     * 收卷的那一刻正是这份卷子定稿的那一刻。
     *
     * @param callable $grade function(int $surveyId, int $responseId): void
     */
    public function withGrading(callable $grade): self
    {
        $this->grade = $grade;
        return $this;
    }

    public function ensureSchema(): void
    {
        $this->attempts->ensureSchema();
    }

    /**
     * 每次 beforeSurveyPage 调用一次。
     *
     * @param int|null $responseId 引擎当前的答卷行号（第一页提交之前是 null）
     * @param string   $nowUtc     权威时刻，只允许由 MjyServerClock 提供
     * @return MjyExamTimer|null 这份问卷没有限时就返回 null
     */
    public function onPage(
        int $surveyId,
        string $sessionKey,
        MjyPolicyDecision $decision,
        ?int $responseId,
        string $nowUtc
    ): ?MjyExamTimer {
        $deadline = $decision->deadlineAt();
        if ($deadline === null) {
            return null;
        }
        $this->attempts->track($surveyId, $sessionKey, $responseId, $deadline, $nowUtc);
        $timer = new MjyExamTimer($deadline, $nowUtc);
        if ($timer->isExpired()) {
            // 先交卷，再由调用方拒绝。
            $attempt = $this->attempts->find($surveyId, $sessionKey);
            if ($this->attempts->forceSubmit($surveyId, $sessionKey, $nowUtc) && $attempt !== null) {
                $this->gradeIfPossible($surveyId, $attempt['response_id']);
            }
        }
        return $timer;
    }

    /**
     * 作答者自己按时交了卷。之后回收作业不再管这一份。
     */
    public function onComplete(int $surveyId, string $sessionKey, string $nowUtc): void
    {
        // 这里不判分：正常交卷由插件在 afterSurveyComplete 里判（那条路不依赖限时策略，
        // 没有限时的考试也要判）。监考只负责两条强制交卷的路径。
        $this->attempts->markSubmitted($surveyId, $sessionKey, $nowUtc);
    }

    /**
     * 把这份问卷里所有到点未交的卷强制交掉（cron）。
     *
     * 直接关掉浏览器的人不会再有请求，`beforeSurveyPage` 挂不上——这是他们唯一的兜底。
     *
     * @return int 结掉了几份
     */
    public function reap(int $surveyId, string $nowUtc): int
    {
        $settled = 0;
        foreach ($this->attempts->expired($surveyId, $nowUtc) as $attempt) {
            if ($this->attempts->forceSubmit($surveyId, $attempt['session_key'], $nowUtc)) {
                $this->gradeIfPossible($surveyId, $attempt['response_id']);
            }
            $settled++;
        }
        return $settled;
    }

    /**
     * 判分失败不影响收卷：卷子已经交上去了，分数可以事后重判；
     * 反过来，为了判分把收卷也一起回滚才是真的丢东西。
     */
    private function gradeIfPossible(int $surveyId, ?int $responseId): void
    {
        if ($this->grade === null || $responseId === null) {
            return;
        }
        ($this->grade)($surveyId, $responseId);
    }
}
