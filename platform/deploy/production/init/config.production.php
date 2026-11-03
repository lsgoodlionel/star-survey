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

// Docker DNS/IP is trusted only within the managed same-host network. It is
// not cryptographic identity and does not protect against Docker-host control.
$trustedGatewayRequest = static function ($remoteAddress, $requestUri, $method, $gatewayAddresses) {
    if (!is_string($remoteAddress) || filter_var($remoteAddress, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false
        || !is_array($gatewayAddresses) || !in_array($remoteAddress, $gatewayAddresses, true)
        || !is_string($requestUri) || !is_string($method)) {
        return false;
    }
    $path = parse_url($requestUri, PHP_URL_PATH);
    $query = parse_url($requestUri, PHP_URL_QUERY);
    $params = array();
    $seen = array();
    if (is_string($query)) {
        foreach (explode('&', $query) as $part) {
            $key = urldecode(explode('=', $part, 2)[0]);
            $normalized = str_replace(array(' ', '.'), '_', $key);
            $base = explode('[', $normalized, 2)[0];
            if (in_array($base, array('plugin', 'function', 'r'), true)) {
                if ($key !== $base || isset($seen[$base])) return false;
                $seen[$base] = true;
            }
        }
        parse_str($query, $params);
    }
    if (isset($params['r'])) return false;
    if ($method === 'POST' && $path === '/index.php/admin/remotecontrol') {
        return true;
    }
    if ($method === 'POST' && $path === '/index.php/plugins/direct/plugin/MjyQuestionExtensions/function/brandAssetsV1') {
        return (!isset($params['plugin']) || $params['plugin'] === 'MjyQuestionExtensions')
            && (!isset($params['function']) || $params['function'] === 'brandAssetsV1');
    }
    if ($method !== 'GET' || $path !== '/index.php/plugins/direct') {
        return false;
    }
    if (!is_string($query)) return false;
    $plugin = $params['plugin'] ?? null;
    $function = $params['function'] ?? null;
    return ($plugin === 'MjyRuntimePolicy' && $function === 'policyStatus')
        || ($plugin === 'MjyQuestionExtensions' && in_array($function, array('extensionAnswers', 'attachmentFile'), true));
};
$applyTrustedScheme = static function (&$server, $edgeAddresses, $gatewayAddresses) use ($trustedGatewayRequest) {
    $scheme = $server['HTTP_X_FORWARDED_PROTO'] ?? null;
    unset($server['HTTP_X_FORWARDED_PROTO'], $server['HTTP_FORWARDED_PROTO']);
    $remote = $server['REMOTE_ADDR'] ?? '';
    if (is_string($remote) && filter_var($remote, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) !== false
        && is_array($edgeAddresses) && in_array($remote, $edgeAddresses, true) && $scheme === 'https') {
        $server['HTTP_X_FORWARDED_PROTO'] = 'https';
    } elseif ($trustedGatewayRequest($remote, $server['REQUEST_URI'] ?? '', $server['REQUEST_METHOD'] ?? '', $gatewayAddresses)) {
        $server['HTTP_X_FORWARDED_PROTO'] = 'https';
    }
};
if (!empty($_SERVER['REMOTE_ADDR'])) {
    $applyTrustedScheme($_SERVER, @gethostbynamel('edge') ?: array(), @gethostbynamel('publish-gateway') ?: array());
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
