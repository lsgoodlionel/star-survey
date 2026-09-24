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
    /** 货架题的货架图与商品热区（R02-18）。 */
    public const SHELF_IMAGE = 'mjy_shelf_image';
    public const SHELF_PRODUCTS = 'mjy_shelf_products';
    /** 文字点睛的原文与可标记片段（R02-22，JSON）。 */
    public const HIGHLIGHT_TEXT = 'mjy_highlight_text';
    public const HIGHLIGHT_SEGMENTS = 'mjy_highlight_segments';
    /** 心理实验的试次（R02-46，JSON）：刺激与**正确按键**；正确率由平台按它推导。 */
    public const PSYCH_TRIALS = 'mjy_psych_trials';
    /** 专业模型（R02-47）：模型名与该模型的采集对象；读端按模型名取分析口径。 */
    public const MODEL_NAME = 'mjy_model_name';
    public const MODEL_FEATURES = 'mjy_model_features';
    /**
     * 多级下拉（R02-03）引用哪本字典、哪一版、那一版的内容摘要，以及每一级的标题。
     * 取值集合刻意不在这里：它们随快照走 plugin_settings，物化进 MjyDictionaryStore。
     */
    public const DICTIONARY = 'mjy_dictionary';
    public const DICTIONARY_VERSION = 'mjy_dictionary_version';
    public const DICTIONARY_DIGEST = 'mjy_dictionary_digest';
    public const DICTIONARY_LEVELS = 'mjy_dictionary_levels';
    /** 轮播图的幻灯片（R02-20，JSON）：图来自平台资产服务，地址带签名与过期时刻（ADR 0019）。 */
    public const CAROUSEL_SLIDES = 'mjy_carousel_slides';
    public const CAROUSEL_AUTOPLAY = 'mjy_carousel_autoplay';

    private const CATEGORY = 'MJY 结构化题型';
    private const CATEGORY_GROUPS = 'MJY 选项分类';
    private const CATEGORY_CAROUSEL = 'MJY 轮播图';

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
            self::SHELF_IMAGE => self::definition($structured, 90, 'text', '', '货架图地址', [
                'help' => '作答者在这张图上点选；净化会改写 URL 里的 & ，所以不过滤',
            ]),
            self::SHELF_PRODUCTS => self::definition($structured, 100, 'textarea', '', '商品与热区（JSON）', [
                'help' => '形如 [{"code":"S1","label":"牛奶","x":0.1,"y":0.2,"w":0.2,"h":0.3}]，坐标归一化到 [0,1]',
            ]),
            self::HIGHLIGHT_TEXT => self::definition($structured, 110, 'textarea', '', '文字点睛的原文', [
                'help' => '片段偏移按这段原文的字符计；改了原文就必须换结构版本',
            ]),
            self::HIGHLIGHT_SEGMENTS => self::definition($structured, 120, 'textarea', '', '可标记的片段（JSON）', [
                'help' => '形如 [{"code":"s0_4_9f8c2a","start":0,"length":4}]，按偏移升序且互不重叠',
            ]),
            self::PSYCH_TRIALS => self::definition($structured, 130, 'textarea', '', '试次（JSON）', [
                'help' => '形如 [{"code":"T1","label":"第一试次","stimulus":"红","correct":"left"}]；'
                    . 'correct 只用于平台推导正确率，浏览器判出来的对错不作数',
            ]),
            self::MODEL_NAME => self::definition($structured, 140, 'text', '', '专业模型名', [
                'help' => '读端按它取对应的分析口径，例如 kano',
            ]),
            self::MODEL_FEATURES => self::definition($structured, 150, 'textarea', '', '模型采集对象（JSON）', [
                'help' => '形如 [{"code":"F1","label":"夜间模式"}]，一个对象一行；量表由模型固定，不在这里',
            ]),
            self::DICTIONARY => self::definition($structured, 210, 'text', '', '引用的字典', [
                'help' => '平台字典代码，如 cn-admin-divisions',
            ]),
            self::DICTIONARY_VERSION => self::definition($structured, 220, 'text', '', '字典版本', [
                'help' => '由平台在发布时固化，作者改不了',
            ]),
            self::DICTIONARY_DIGEST => self::definition($structured, 230, 'text', '', '字典内容摘要', [
                'help' => '插件据它判断引擎上装着的那份是不是同一份',
            ]),
            self::DICTIONARY_LEVELS => self::definition($structured, 240, 'textarea', '', '各级标题（JSON）', [
                'help' => '形如 ["省","市","区"]，一级一列',
            ]),
            // R02-20 轮播图：数据形状就是原生单选，所以挂在 L 上而不是结构化题型上。
            // 地址里带签名，& 一旦被净化改写，图就全裂——必须走 xssfilter => false。
            self::CAROUSEL_SLIDES => self::definition(
                Question::QT_L_LIST,
                10,
                'textarea',
                '',
                '幻灯片（JSON）',
                [
                    'help' => '由平台按资产引用生成，形如 [{"code":"A1","url":"…","alt":"…","assetVersion":1}]',
                    'category' => self::CATEGORY_CAROUSEL,
                ]
            ),
            self::CAROUSEL_AUTOPLAY => self::definition(
                Question::QT_L_LIST,
                20,
                'integer',
                '0',
                '自动轮播',
                [
                    'help' => '1＝自动切换；作答者一动就停',
                    'category' => self::CATEGORY_CAROUSEL,
                    'xssfilter' => true,
                ]
            ),
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
