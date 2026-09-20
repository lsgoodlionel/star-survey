<?php

/**
 * P0-00.7 考试与配额验证栈的引擎配置模板。
 *
 * 起栈时复制到 .runtime/config.php 再挂载进容器，避免引擎回写时弄脏仓库模板。
 * 口令只用于本地验证栈。
 */

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

return array(
    'components' => array(
        'db' => array(
            'connectionString' => 'mysql:host=db;port=3306;dbname=exam;',
            'emulatePrepare' => true,
            'username' => 'exam',
            'password' => 'exam-db-pass',
            'charset' => 'utf8mb4',
            'tablePrefix' => 'lime_',
        ),

        'session' => array(
            // 与其他栈同域（localhost）共存时，PHPSESSID 会互相覆盖登录态，
            // 见 P0-00.5 发现 9。这里给本栈一个独立的会话名。
            'sessionName' => 'EXAMSESSID',
        ),

        'urlManager' => array(
            'urlFormat' => 'path',
            'rules' => array(),
            'showScriptName' => true,
        ),
    ),
    'config' => array(
        'editorEnabled' => false,
        'debug' => 0,
        'debugsql' => 0,
    )
);
