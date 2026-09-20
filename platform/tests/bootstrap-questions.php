<?php

/**
 * P0-00.3 题型纵切测试套件引导。
 *
 * 先走引擎自带的 bootstrap，再显式引入插件根目录下被测试直接引用的类：
 * 这些类平时由引擎在加载插件时 Yii::import，但纯单元测试（校验器、副表）
 * 不经过插件加载流程，所以在这里补上。
 */

require_once dirname(__DIR__, 2) . '/tests/bootstrap.php';

$pluginRoot = dirname(__DIR__, 2) . '/plugins/MjyQuestionExtensions';
foreach (glob($pluginRoot . '/*.php') ?: [] as $classFile) {
    require_once $classFile;
}
