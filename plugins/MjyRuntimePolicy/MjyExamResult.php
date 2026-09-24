<?php

/**
 * 一份卷子的判分结果（WP-10）。不可变。
 *
 * 明细里**只有对错与得分，没有正确答案**：成绩是要给人看、要存进成绩表、
 * 将来还要导出的东西，正确答案跟着它跑一圈就等于绕过 WP-09.1 又下发了一次。
 */
class MjyExamResult
{
    /** @var float */
    private $score;

    /** @var float */
    private $maxScore;

    /** @var array<string, array{correct: bool, points: float, answered: bool}> */
    private $detail;

    /**
     * @param array<string, array{correct: bool, points: float, answered: bool}> $detail
     */
    public function __construct(float $score, float $maxScore, array $detail)
    {
        $this->score = $score;
        $this->maxScore = $maxScore;
        $this->detail = $detail;
    }

    public function score(): float
    {
        return $this->score;
    }

    public function maxScore(): float
    {
        return $this->maxScore;
    }

    public function questionCount(): int
    {
        return count($this->detail);
    }

    public function correctCount(): int
    {
        return count(array_filter($this->detail, static fn(array $item): bool => $item['correct']));
    }

    /**
     * @return array<string, array{correct: bool, points: float, answered: bool}>
     */
    public function detail(): array
    {
        return $this->detail;
    }

    public function detailJson(): string
    {
        return (string) json_encode($this->detail, JSON_UNESCAPED_UNICODE);
    }
}
