<?php

if (!defined('BASEPATH')) {
    exit('No direct script access allowed');
}

$productionSecret = static function ($environmentName) {
    $path = (string) getenv($environmentName);
    if ($path === '' || !is_readable($path)) {
        throw new RuntimeException($environmentName . ' does not point to a readable secret.');
    }
    $value = file_get_contents($path);
    if ($value === false || trim($value) === '') {
        throw new RuntimeException($environmentName . ' is empty.');
    }
    return rtrim($value, "\r\n");
};

// The publish gateway reaches the engine over the isolated Docker network.
// Preserve force_ssl for every other caller and every other route; the gateway's
// RemoteControl credentials and plugin signatures remain mandatory.
$trustedGatewayRequest = static function ($remoteAddress, $requestUri, $method, $gatewayAddresses) {
    if (!is_string($remoteAddress) || filter_var($remoteAddress, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false
        || !is_array($gatewayAddresses) || !in_array($remoteAddress, $gatewayAddresses, true)
        || !is_string($requestUri) || !is_string($method)) {
        return false;
    }
    $path = parse_url($requestUri, PHP_URL_PATH);
    return ($method === 'POST' && $path === '/index.php/admin/remotecontrol')
        || ($method === 'GET' && $path === '/index.php/plugins/direct');
};
if (!empty($_SERVER['REMOTE_ADDR'])) {
    $gatewayAddresses = gethostbynamel('publish-gateway') ?: array();
    if ($trustedGatewayRequest($_SERVER['REMOTE_ADDR'], $_SERVER['REQUEST_URI'] ?? '',
        $_SERVER['REQUEST_METHOD'] ?? '', $gatewayAddresses)) {
        $_SERVER['HTTP_X_FORWARDED_PROTO'] = 'https';
    }
}

$baseUrl = '/survey';
$forwardedPrefix = $_SERVER['HTTP_X_FORWARDED_PREFIX'] ?? '';
if (in_array($forwardedPrefix, array('/survey', '/engine-admin'), true)) {
    $baseUrl = $forwardedPrefix;
}

return array(
    'components' => array(
        'db' => array(
            'connectionString' => sprintf(
                'mysql:host=%s;port=3306;dbname=%s;',
                getenv('ENGINE_DB_HOST') ?: 'engine-db',
                getenv('ENGINE_DB_NAME') ?: 'limesurvey'
            ),
            'emulatePrepare' => true,
            'username' => getenv('ENGINE_DB_USER') ?: 'limesurvey',
            'password' => $productionSecret('ENGINE_DB_PASSWORD_FILE'),
            'charset' => 'utf8mb4',
            'tablePrefix' => 'lime_',
        ),
        'request' => array(
            'baseUrl' => $baseUrl,
        ),
        'session' => array(
            'sessionName' => 'SSP_production',
            'cookieParams' => array(
                'secure' => true,
                'httponly' => true,
                'samesite' => 'Lax',
            ),
        ),
        'urlManager' => array(
            'urlFormat' => 'path',
            'rules' => array(),
            'showScriptName' => true,
        ),
    ),
    'config' => array(
        'editorEnabled' => false,
        'force_ssl' => 'on',
        'ssl_disable_alert' => true,
        'debug' => 0,
        'debugsql' => 0,
        'updatable' => false,
        'GeoNamesUsername' => '',
        'googleMapsAPIKey' => '',
        'googletranslateapikey' => '',
        'ipInfoDbAPIKey' => '',
    ),
);
