<?php

/**
 * 私有化实例的可信 Host 白名单（P0-00.8）。
 *
 * 引擎会在管理员首次登录时自动生成 application/config/allowed_hosts.php。
 * 交付时必须由交付方固定，不能让“谁先登录谁说了算”（ADR 0002 F-04）。
 */

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

$hosts = array(
    'localhost',
    'web',
    'survey-private-web',
);

return array('allowedHosts' => $hosts);
