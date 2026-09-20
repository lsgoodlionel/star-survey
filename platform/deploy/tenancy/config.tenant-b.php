<?php

/**
 * 租户 B 引擎实例的配置（P0-00.5 租户隔离验证栈专用）。
 *
 * 关键点：每个实例只持有自己那一套数据库连接信息，数据库账号 tenant_b 在
 * 自己的 DB 服务器上只被授予 tenant_b 库的权限。实例之间没有任何共享的
 * 连接串、账号或密钥。
 *
 * 口令仅用于本地验证栈。
 */

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

return array(
    'components' => array(
        'db' => array(
            'connectionString' => 'mysql:host=db-b;port=3306;dbname=tenant_b;',
            'emulatePrepare' => true,
            'username' => 'tenant_b',
            'password' => 'tenant-b-db-pass',
            'charset' => 'utf8mb4',
            'tablePrefix' => 'lime_',
        ),

        'urlManager' => array(
            'urlFormat' => 'path',
            'rules' => array(
            ),
            'showScriptName' => true,
        ),
    ),
    'config' => array(
        'editorEnabled' => false,
        'debug' => 0,
        'debugsql' => 0,
    ),
);
