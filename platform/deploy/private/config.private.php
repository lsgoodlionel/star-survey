<?php

/**
 * 私有化单节点实例的引擎配置（P0-00.8）。
 *
 * 与 dev 配置的差别：debug 全关、编辑器关闭，并把 ADR 0002 决定 4 要求的
 * 按实例会话 cookie 名固定下来（引擎默认全叫 PHPSESSID，同域多实例会互相
 * 覆盖登录态）。
 */

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

return array(
    'components' => array(
        'db' => array(
            'connectionString' => 'mysql:host=db;port=3306;dbname=survey_private;',
            'emulatePrepare' => true,
            'username' => 'survey_private',
            'password' => 'private-db-pass',
            'charset' => 'utf8mb4',
            'tablePrefix' => 'lime_',
        ),

        'session' => array(
            // ADR 0002 决定 4：会话 cookie 按实例命名。
            'sessionName' => 'SSP_private',
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
        // 私有化交付：禁止引擎自查更新（见 private-deployment.md 的出网清单）。
        'updatable' => false,
        // 地图题的第三方服务：引擎默认 GeoNamesUsername='limesurvey'，开箱即外联。
        // 注意这只堵住服务端注入的用户名，assets/scripts/map.js 里的域名是写死的，
        // 必须另行打补丁（见 private-deployment.md 的 P-1～P-3 补丁项）。
        'GeoNamesUsername' => '',
        'googleMapsAPIKey' => '',
        'googletranslateapikey' => '',
        'ipInfoDbAPIKey' => '',
    ),
);
