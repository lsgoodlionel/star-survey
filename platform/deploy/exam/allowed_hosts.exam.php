<?php

/**
 * P0-00.7 验证栈的可信 Host 列表。
 *
 * 引擎会在管理员首次登录时自动生成 application/config/allowed_hosts.php；
 * 本栈与 dev / test / tenancy 共享同一份代码目录，自动生成会互相覆盖
 * （P0-00.5 发现 8）。这里固定挂一份，只允许本栈用得到的主机名。
 */

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

$hosts = array(
    'localhost',
    'web',
);

return array('allowedHosts' => $hosts);
