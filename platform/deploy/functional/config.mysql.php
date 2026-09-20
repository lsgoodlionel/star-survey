<?php

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}
/*
| P0-00.2 functional 栈的引擎配置。
|
| 与 platform/deploy/test/config.mysql.php 同源（= config-sample-mysql.php 只改
| 数据库主机），额外按 .github/workflows/functional.yml 的要求：
|   - editorEnabled=false：CI 在 functional 套件里显式关掉新编辑器。
|   - debug=0：CI 注释掉了 debug=2，因为 InstallationControllerTest 会重写
|     config.php，而 YII_DEBUG 在 bootstrap 阶段就固化了，两边不一致会让
|     phpunit 用 CDummyCache、Apache 用 CFileCache，缓存 flush 变成空操作。
|
| 数据库主机是 compose 服务名 db（项目 survey-functional）。
*/
return array(
    'components' => array(
        'db' => array(
            'connectionString' => 'mysql:host=db;port=3306;dbname=limesurvey;',
            'emulatePrepare' => true,
            'username' => 'root',
            'password' => 'root',
            'charset' => 'utf8mb4',
            'tablePrefix' => 'lime_',
        ),

        'urlManager' => array(
            'urlFormat' => 'path',
            'rules' => array(
                // You can add your own rules here
            ),
            'showScriptName' => true,
        ),

    ),
    'config' => array(
        'editorEnabled' => false,
        'debug' => 0,
        'debugsql' => 0,
    )
);
/* End of file config.php */
/* Location: ./application/config/config.php */
