<?php

/**
 * 平台下发的考试答案键（契约 survey-exam-v1 §4，载荷 mjy-exam-key/1）。
 *
 * 随 LSS 导入写进 lime_plugin_settings（model='Survey'、key='mjy_exam_key'），
 * 除导入之外没有写路径。**它是整套里唯一一份正确答案**：不在题目属性里、
 * 不在选项的 assessment_value 里、不在任何表达式里，因此引擎渲染作答页时
 * 根本碰不到它（网关侧的保证见 pubgw/exam/，端到端证据见 e2e/exam_key.py）。
 *
 * 解析一律 fail closed：载荷有任何一处不认识就整份拒绝。理由是"半份答案键"
 * 比"没有答案键"更糟——没有答案键判不了分（看得见），半份答案键会把一部分题
 * 判成"怎么答都对"（看不见）。
 */
class MjyExamKey
{
    public const SCHEMA = 'mjy-exam-key/1';

    private const TOP_KEYS = ['schema', 'answerKey'];
    private const ENTRY_KEYS = ['question', 'kind', 'correct', 'points', 'match'];
    private const MATCH_KEYS = ['ignoreCase', 'trim'];

    /** @var MjyExamKeyEntry[] 按载荷顺序 */
    private $entries;

    /** @var string */
    private $digest;

    /**
     * @param MjyExamKeyEntry[] $entries
     */
    private function __construct(array $entries, string $digest)
    {
        $this->entries = $entries;
        $this->digest = $digest;
    }

    /**
     * @throws InvalidArgumentException 载荷不可信
     */
    public static function fromJson(string $text): self
    {
        $payload = json_decode($text, true);
        if (!is_array($payload) || $payload === [] || array_is_list($payload)) {
            throw new InvalidArgumentException('答案键载荷不是 JSON 对象');
        }
        self::rejectUnknownKeys($payload, self::TOP_KEYS, '答案键');
        if (($payload['schema'] ?? null) !== self::SCHEMA) {
            throw new InvalidArgumentException('答案键载荷的 schema 不是 ' . self::SCHEMA);
        }
        return new self(self::parseEntries($payload['answerKey'] ?? null), hash('sha256', $text));
    }

    /**
     * @return MjyExamKeyEntry[]
     */
    private static function parseEntries($raw): array
    {
        if (!is_array($raw) || !array_is_list($raw) || $raw === []) {
            throw new InvalidArgumentException('answerKey 必须是非空数组');
        }
        $entries = [];
        $seen = [];
        foreach ($raw as $index => $item) {
            $entry = self::parseEntry($item, $index);
            if (in_array($entry->code(), $seen, true)) {
                throw new InvalidArgumentException("题目 {$entry->code()} 在答案键里出现了两次");
            }
            $seen[] = $entry->code();
            $entries[] = $entry;
        }
        return $entries;
    }

    private static function parseEntry($raw, int $index): MjyExamKeyEntry
    {
        $where = "answerKey[{$index}]";
        if (!is_array($raw) || array_is_list($raw)) {
            throw new InvalidArgumentException("{$where} 不是对象");
        }
        self::rejectUnknownKeys($raw, self::ENTRY_KEYS, $where);

        $code = $raw['question'] ?? null;
        if (!is_string($code) || $code === '') {
            throw new InvalidArgumentException("{$where}.question 必须是非空字符串");
        }
        $kind = $raw['kind'] ?? null;
        if (!is_string($kind) || !in_array($kind, MjyExamKeyEntry::KINDS, true)) {
            throw new InvalidArgumentException("{$where}.kind 不是认识的题型");
        }
        $points = $raw['points'] ?? null;
        if (!is_int($points) && !is_float($points)) {
            throw new InvalidArgumentException("{$where}.points 必须是数字");
        }
        if ($points <= 0) {
            throw new InvalidArgumentException("{$where}.points 必须是正数");
        }
        [$ignoreCase, $trim] = self::parseMatch($raw['match'] ?? null, $where);

        return new MjyExamKeyEntry($code, $kind, self::parseCorrect($raw['correct'] ?? null, $where),
            (float) $points, $ignoreCase, $trim);
    }

    /**
     * @return string[]
     */
    private static function parseCorrect($raw, string $where): array
    {
        if (!is_array($raw) || !array_is_list($raw) || $raw === []) {
            throw new InvalidArgumentException("{$where}.correct 必须是非空数组");
        }
        foreach ($raw as $answer) {
            if (!is_string($answer) || $answer === '') {
                throw new InvalidArgumentException("{$where}.correct 的每一项都必须是非空字符串");
            }
        }
        return $raw;
    }

    /**
     * @return array{0: bool, 1: bool}
     */
    private static function parseMatch($raw, string $where): array
    {
        if ($raw === null) {
            return [false, true];
        }
        if (!is_array($raw) || array_is_list($raw)) {
            throw new InvalidArgumentException("{$where}.match 不是对象");
        }
        self::rejectUnknownKeys($raw, self::MATCH_KEYS, "{$where}.match");
        foreach (self::MATCH_KEYS as $name) {
            if (array_key_exists($name, $raw) && !is_bool($raw[$name])) {
                throw new InvalidArgumentException("{$where}.match.{$name} 必须是布尔值");
            }
        }
        return [$raw['ignoreCase'] ?? false, $raw['trim'] ?? true];
    }

    /**
     * 未知键一律拒绝：网关加了新语义而插件还是旧版时，宁可整份不认，
     * 也不要按旧语义判出一份看起来正常的成绩。
     *
     * @param string[] $allowed
     */
    private static function rejectUnknownKeys(array $payload, array $allowed, string $where): void
    {
        $unknown = array_diff(array_keys($payload), $allowed);
        if ($unknown !== []) {
            throw new InvalidArgumentException("{$where} 有不认识的键：" . implode('、', $unknown));
        }
    }

    /**
     * @return string[]
     */
    public function questionCodes(): array
    {
        return array_map(static fn(MjyExamKeyEntry $entry): string => $entry->code(), $this->entries);
    }

    /**
     * @return MjyExamKeyEntry[]
     */
    public function entries(): array
    {
        return $this->entries;
    }

    public function find(string $code): ?MjyExamKeyEntry
    {
        foreach ($this->entries as $entry) {
            if ($entry->code() === $code) {
                return $entry;
            }
        }
        return null;
    }

    public function totalPoints(): float
    {
        $total = 0.0;
        foreach ($this->entries as $entry) {
            $total += $entry->points();
        }
        return $total;
    }

    public function digest(): string
    {
        return $this->digest;
    }
}
