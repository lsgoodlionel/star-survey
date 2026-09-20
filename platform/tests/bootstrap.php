<?php

/**
 * 平台测试套件引导：先走引擎自带的 bootstrap，再加载插件里被测试直接引用的类。
 * 插件类平时由引擎在加载插件时导入，测试替身在类声明阶段就需要接口，故在此显式引入。
 */

require_once dirname(__DIR__, 2) . '/tests/bootstrap.php';
require_once dirname(__DIR__, 2) . '/plugins/MjyPlatformBridge/MjyEventTransport.php';
require_once __DIR__ . '/../../plugins/MjyPlatformBridge/tests/FakeEventTransport.php';
