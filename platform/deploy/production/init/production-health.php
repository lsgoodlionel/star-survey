<?php

header('Cache-Control: no-store');
header('Content-Type: text/plain; charset=UTF-8');
http_response_code(503);

try {
    defined('BASEPATH') or define('BASEPATH', __DIR__);
    $config = require '/var/www/html/application/config/config.php';
    $database = $config['components']['db'];
    $prefix = $database['tablePrefix'];
    $pdo = new PDO(
        $database['connectionString'],
        $database['username'],
        $database['password'],
        array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION)
    );

    $requiredSettings = array(
        'mjy_production_init_v1' => '1',
        'RPCInterface' => 'json',
        'force_ssl' => 'on',
    );
    $settingQuery = $pdo->prepare(
        'SELECT stg_value FROM ' . $prefix . 'settings_global WHERE stg_name = ?'
    );
    foreach ($requiredSettings as $name => $expected) {
        $settingQuery->execute(array($name));
        if ((string) $settingQuery->fetchColumn() !== $expected) {
            throw new RuntimeException('required setting is not ready');
        }
    }

    $username = trim((string) getenv('ENGINE_ADMIN_USER'));
    if ($username === '') {
        throw new RuntimeException('administrator identity is not configured');
    }
    $adminQuery = $pdo->prepare(
        'SELECT COUNT(*) FROM ' . $prefix . 'users u '
        . 'JOIN ' . $prefix . 'permissions p ON p.uid = u.uid '
        . "WHERE u.users_name = ? AND p.entity = 'global' AND p.entity_id = 0 "
        . "AND p.permission = 'superadmin' AND p.read_p = 1"
    );
    $adminQuery->execute(array($username));
    if ((int) $adminQuery->fetchColumn() !== 1) {
        throw new RuntimeException('production administrator is not ready');
    }

    $pluginQuery = $pdo->query(
        'SELECT COUNT(*) FROM ' . $prefix . 'plugins '
        . "WHERE active = 1 AND name IN ('MjyPlatformBridge','MjyRuntimePolicy','MjyQuestionExtensions')"
    );
    if ((int) $pluginQuery->fetchColumn() !== 3) {
        throw new RuntimeException('required plugins are not ready');
    }

    http_response_code(204);
} catch (Throwable $error) {
    error_log('production health check failed: ' . get_class($error));
    echo "unavailable\n";
}
