<?php

/**
 * 通过 newQuestionAttributes 事件注册的题目属性。
 *
 * 引擎不认识这些属性，但**照样会把它们随 .lss 导出与导入**
 * （`application/helpers/admin/import_helper.php:3053` 起没有白名单）。
 * 唯一的坑是 `QuestionAttribute::filterXss()`：只有当属性定义里写了
 * `xssfilter => false` 时才跳过 HTML 净化，而属性定义来自本插件 ——
 * 插件没启用时导入这份 .lss，列定义 JSON 会被 HTMLPurifier 改写。
 */
class MjyQuestionAttributeDefinitions
{
    public const COLUMNS = 'mjy_table_columns';
    public const MIN_ROWS = 'mjy_table_min_rows';
    public const MAX_ROWS = 'mjy_table_max_rows';

    private const CATEGORY = 'MJY 自增表格';

    /**
     * @return array<string, array<string, mixed>>
     */
    public static function all(): array
    {
        $types = Question::QT_T_LONG_FREE_TEXT;
        return [
            self::COLUMNS => [
                'types' => $types,
                'category' => self::CATEGORY,
                'sortorder' => 10,
                'inputtype' => 'textarea',
                'default' => '',
                'xssfilter' => false,
                'caption' => '列定义（JSON）',
                'help' => '形如 [{"code":"item","label":"名称","type":"text","required":true}]',
            ],
            self::MIN_ROWS => [
                'types' => $types,
                'category' => self::CATEGORY,
                'sortorder' => 20,
                'inputtype' => 'integer',
                'default' => '0',
                'caption' => '最少行数',
                'help' => '0 表示允许不填',
            ],
            self::MAX_ROWS => [
                'types' => $types,
                'category' => self::CATEGORY,
                'sortorder' => 30,
                'inputtype' => 'integer',
                'default' => '20',
                'caption' => '最多行数',
                'help' => '服务端强制，浏览器端只是提示',
            ],
        ];
    }
}
