<?php

/**
 * 一道客观题的答案与分值（契约 survey-exam-v1 §4）。
 *
 * 不可变：从载荷里解析出来之后没有任何写入口。判分只读它，不改它。
 *
 * 这个对象**只活在服务端**。它没有 toArray()、没有 jsonSerialize()，
 * 就是为了让"顺手把它序列化进页面"这件事做不到——WP-09 的要害是答案不下发，
 * 类型上堵死比写注释提醒可靠。
 */
class MjyExamKeyEntry
{
    public const KIND_CHOICE = 'choice';
    public const KIND_SET = 'set';
    public const KIND_TEXT = 'text';
    public const KIND_NUMBER = 'number';

    public const KINDS = [self::KIND_CHOICE, self::KIND_SET, self::KIND_TEXT, self::KIND_NUMBER];

    /** @var string */
    private $code;

    /** @var string */
    private $kind;

    /** @var string[] */
    private $correct;

    /** @var float */
    private $points;

    /** @var bool */
    private $ignoreCase;

    /** @var bool */
    private $trim;

    /**
     * @param string[] $correct
     */
    public function __construct(
        string $code,
        string $kind,
        array $correct,
        float $points,
        bool $ignoreCase = false,
        bool $trim = true
    ) {
        $this->code = $code;
        $this->kind = $kind;
        $this->correct = array_values($correct);
        $this->points = $points;
        $this->ignoreCase = $ignoreCase;
        $this->trim = $trim;
    }

    public function code(): string
    {
        return $this->code;
    }

    public function kind(): string
    {
        return $this->kind;
    }

    /**
     * @return string[]
     */
    public function correct(): array
    {
        return $this->correct;
    }

    public function points(): float
    {
        return $this->points;
    }

    public function ignoreCase(): bool
    {
        return $this->ignoreCase;
    }

    public function trim(): bool
    {
        return $this->trim;
    }
}
