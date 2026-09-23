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

    /** 热力图选区（R02-19）：同一套 JSON 信封与副表，列定义由平台生成。 */
    public const HEATMAP_THEME = 'mjy-heatmap';

    /** 循环评价（R02-11）：一行一个评价对象，列定义由平台按维度与量表生成。 */
    public const LOOP_RATING_THEME = 'mjy-loop-rating';

    /** 图片 PK（R02-17）：整题一行，一对一列，列的取值恰好是这一对的两张图。 */
    public const IMAGE_PK_THEME = 'mjy-image-pk';

    /** 货架题（R02-18）：一行一件商品，商品列枚举＋唯一，件数列整数。 */
    public const SHELF_THEME = 'mjy-shelf';

    /** 文字点睛（R02-22）：一行一处标记，片段列枚举＋唯一，标记列枚举。 */
    public const TEXT_HIGHLIGHT_THEME = 'mjy-text-highlight';

    /** 心理实验（R02-46）：一行一个试次，试次列枚举＋唯一，按键列枚举，反应时是有界整数。 */
    public const PSYCH_TRIAL_THEME = 'mjy-psych-trial';

    /**
     * 作答走「JSON 信封＋副表」的全部题型主题（ADR 0006 的 C 档）。
     * 与发布网关 pubgw/questions/themes.py 的 STRUCTURED_THEMES 一一对应
     * （网关侧 tests/test_plugin_registry_parity.py 按源码文本对一遍：
     * 少列一个在引擎上表现为「这道题的作答无人校验」，是静默故障）。
     */
    public const STRUCTURED_THEMES = [
        self::REPEATING_TABLE_THEME,
        self::HEATMAP_THEME,
        self::LOOP_RATING_THEME,
        self::IMAGE_PK_THEME,
        self::SHELF_THEME,
        self::TEXT_HIGHLIGHT_THEME,
        self::PSYCH_TRIAL_THEME,
    ];

    /** @var array<string, array<string, mixed>> */
    private $structuredQuestions;

    /** @var array<string, array<string, mixed>> */
    private $uploads;

    /**
     * @param array<string, array<string, mixed>> $structuredQuestions
     * @param array<string, array<string, mixed>> $uploads
     */
    private function __construct(array $structuredQuestions, array $uploads)
    {
        $this->structuredQuestions = $structuredQuestions;
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

        $structuredQuestions = [];
        $uploads = [];
        foreach ($rows as $row) {
            $entry = [
                'qid' => (int) $row['qid'],
                'code' => (string) $row['title'],
                'fieldName' => 'Q' . (int) $row['qid'],
            ];
            if (in_array((string) $row['question_theme_name'], self::STRUCTURED_THEMES, true)) {
                $entry['theme'] = (string) $row['question_theme_name'];
                $structuredQuestions[$entry['code']] = $entry;
                continue;
            }
            if ((string) $row['type'] === Question::QT_VERTICAL_FILE_UPLOAD) {
                $uploads[$entry['code']] = $entry;
            }
        }
        return new self($structuredQuestions, $uploads);
    }

    /**
     * @return array<string, array<string, mixed>> 题目代码 => 题目信息
     */
    public function structuredQuestions(): array
    {
        return $this->structuredQuestions;
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
