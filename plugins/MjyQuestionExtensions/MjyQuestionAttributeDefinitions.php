<?php

/**
 * 通过 newQuestionAttributes 事件注册的题目属性。
 *
 * 引擎不认识这些属性，但**照样会把它们随 .lss 导出与导入**
 * （`application/helpers/admin/import_helper.php:3053` 起没有白名单）。
 * 唯一的坑是 `QuestionAttribute::filterXss()`：只有当属性定义里写了
 * `xssfilter => false` 时才跳过 HTML 净化，而 `xssfilter` 必须是真正的布尔值——
 * 题型主题 config.xml 里的 `<xssfilter>false</xssfilter>` 会变成字符串 'false'，
 * `== false` 不成立，起不到作用。所以**凡是存 JSON 的属性都必须在这里注册**，
 * 哪怕它属于一个纯展示的 B 档主题（ADR 0006 限制 8：插件停用时导入会改写这些值）。
 */
class MjyQuestionAttributeDefinitions
{
    public const COLUMNS = 'mjy_table_columns';
    public const MIN_ROWS = 'mjy_table_min_rows';
    public const MAX_ROWS = 'mjy_table_max_rows';
    /** 副表结构版本：发布时由平台声明，副表按它给每个单元格打标（platform/contracts/question-extension-tables-v1.md）。 */
    public const STRUCTURE_VERSION = 'mjy_structure_version';
    /** 热力图底图地址（R02-19）。 */
    public const HEATMAP_IMAGE = 'mjy_heatmap_image';
    /** 选项分组定义（R02-04，JSON）。 */
    public const OPTION_GROUPS = 'mjy_option_groups';
    /** 循环评价的评价对象（R02-11，JSON）：一行一个对象，标签由主题渲染成行首。 */
    public const LOOP_OBJECTS = 'mjy_loop_objects';
    /** 图片 PK 的参赛图片与配对（R02-17，JSON）。 */
    public const PK_ITEMS = 'mjy_pk_items';
    public const PK_PAIRS = 'mjy_pk_pairs';

    private const CATEGORY = 'MJY 结构化题型';
    private const CATEGORY_GROUPS = 'MJY 选项分类';

    /**
     * @return array<string, array<string, mixed>>
     */
    public static function all(): array
    {
        $structured = Question::QT_T_LONG_FREE_TEXT;
        return [
            self::COLUMNS => self::definition($structured, 10, 'textarea', '', '列定义（JSON）', [
                'help' => '形如 [{"code":"item","label":"名称","type":"text","required":true}]',
            ]),
            self::MIN_ROWS => self::definition($structured, 20, 'integer', '0', '最少行数', [
                'help' => '0 表示允许不填',
                'xssfilter' => true,
            ]),
            self::MAX_ROWS => self::definition($structured, 30, 'integer', '20', '最多行数', [
                'help' => '服务端强制，浏览器端只是提示',
                'xssfilter' => true,
            ]),
            self::STRUCTURE_VERSION => self::definition($structured, 40, 'text', '', '副表结构版本', [
                'help' => '平台发布时声明；改了列定义就要换一个版本，否则旧答卷读不回来',
            ]),
            self::HEATMAP_IMAGE => self::definition($structured, 50, 'text', '', '热力图底图地址', [
                'help' => '作答者在这张图上点选；净化会改写 URL 里的 & ，所以不过滤',
            ]),
            self::LOOP_OBJECTS => self::definition($structured, 60, 'textarea', '', '评价对象（JSON）', [
                'help' => '形如 [{"code":"B1","label":"甲品牌"}]，一个对象一行',
            ]),
            self::PK_ITEMS => self::definition($structured, 70, 'textarea', '', '参赛图片（JSON）', [
                'help' => '形如 [{"code":"A","label":"包装甲","image":"https://…/a.png"}]',
            ]),
            self::PK_PAIRS => self::definition($structured, 80, 'textarea', '', '配对（JSON）', [
                'help' => '形如 [{"code":"P1","left":"A","right":"B"}]',
            ]),
            // R02-04 的两支：单选按答案选项分组，多选按子题分组，共用同一份 JSON。
            // 少写一个题型字母，引擎导入那种题时会把这个属性丢掉，主题拿到空分组静默平铺。
            self::OPTION_GROUPS => self::definition(
                Question::QT_L_LIST . Question::QT_M_MULTIPLE_CHOICE,
                10,
                'textarea',
                '',
                '选项分组定义（JSON）',
                ['help' => '形如 [{"label":"水果","codes":["A1","A2"]}]', 'category' => self::CATEGORY_GROUPS]
            ),
        ];
    }

    /**
     * @param array<string, mixed> $extra
     * @return array<string, mixed>
     */
    private static function definition(
        string $types,
        int $sortorder,
        string $inputtype,
        string $default,
        string $caption,
        array $extra = []
    ): array {
        return array_merge([
            'types' => $types,
            'category' => self::CATEGORY,
            'sortorder' => $sortorder,
            'inputtype' => $inputtype,
            'default' => $default,
            // 缺省不过滤：这些属性存的是 JSON 与 URL，HTMLPurifier 会把引号与 & 改写掉。
            'xssfilter' => false,
            'caption' => $caption,
            'help' => '',
        ], $extra);
    }
}
