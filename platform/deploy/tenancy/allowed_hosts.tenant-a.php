<?php

/**
 * 租户 A 实例的可信 Host 列表（P0-00.5 验证栈专用）。
 *
 * 引擎会在管理员首次登录时自动生成 application/config/allowed_hosts.php。
 * 在本验证栈里代码树是两个实例共享的，自动生成会让一个租户的登录决定另一个
 * 租户能接受哪些域名——所以这里按租户各挂一份，把这个变量固定下来。
 */

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

$hosts = array(
    'localhost',
    'web-a',
);

return array('allowedHosts' => $hosts);
