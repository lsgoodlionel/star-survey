<?php

/**
 * 一次结构化作答的校验结果。
 *
 * 不可变：校验器只产出新对象，不回头改写已经产出的结果。
 */
class MjyValidationResult
{
    /** @var string[] */
    private $errors;

    /** @var array<int, array<string, string>> */
    private $rows;

    /**
     * @param string[] $errors
     * @param array<int, array<string, string>> $rows 归一化后的行；无效时为空
     */
    private function __construct(array $errors, array $rows)
    {
        $this->errors = $errors;
        $this->rows = $rows;
    }

    /**
     * @param array<int, array<string, string>> $rows
     */
    public static function valid(array $rows): self
    {
        return new self([], $rows);
    }

    /**
     * @param string[] $errors
     */
    public static function invalid(array $errors): self
    {
        return new self($errors, []);
    }

    public function isValid(): bool
    {
        return $this->errors === [];
    }

    /**
     * @return string[]
     */
    public function errors(): array
    {
        return $this->errors;
    }

    /**
     * @return array<int, array<string, string>>
     */
    public function rows(): array
    {
        return $this->rows;
    }

    public function rowCount(): int
    {
        return count($this->rows);
    }

    /**
     * 回写给引擎答卷表的规范形式。无效时返回空信封，由调用方决定是否采用。
     */
    public function normalisedJson(): string
    {
        return (string) json_encode(
            ['v' => MjyRepeatingTableValidator::ENVELOPE_VERSION, 'rows' => $this->rows],
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
    }

    public function errorText(): string
    {
        return implode('；', $this->errors);
    }
}
