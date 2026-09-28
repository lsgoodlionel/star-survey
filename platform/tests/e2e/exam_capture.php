<?php

/**
 * WP-09.1 端到端取证：把一次真实作答请求**收到的全部东西**抓下来。
 *
 * 在 <prefix>-test-web 里运行，由 platform/tests/e2e/exam_key.py 驱动：
 *
 *   php platform/tests/e2e/exam_capture.php <cookie-jar> < plan.json
 *
 * 计划：{"url": "/index.php/123?newtest=Y", "needles": ["…"], "headers": ["…"]}
 *
 * 抓的不只是 HTML。作答页真正下发给浏览器的还有它引用的每一个脚本与样式
 * （`<script src>`、`<link rel=stylesheet>`），引擎会把表达式翻成 JS 放在那里面
 * （em_manager_helper.php:4351），所以只看 HTML 的断言抓不到最要紧的那一类泄漏。
 *
 * 输出：
 *   html     整页原文（给差分用：两份只差"哪个选项是对的"的问卷，页面应当逐字节相同）
 *   assets   每个资源的 URL、字节数、SHA-256（差分比哈希，不必把 jQuery 搬来搬去）
 *   hits     每个哨兵串在 HTML 或某个资源里出现的位置与上下文（给"不下发"断言用）
 *
 * 只抓同源资源：跨站的 CDN 不可能拿到本问卷的答案，抓它只会让测试依赖外网。
 */

declare(strict_types=1);

const HOST = 'http://localhost';
const HTTP_TIMEOUT_SECONDS = 60;
const EXCERPT_RADIUS = 80;
const MAX_ASSETS = 200;

exit(main($argv));

function main(array $argv): int
{
    $jar = (string) ($argv[1] ?? '');
    $plan = json_decode((string) stream_get_contents(STDIN), true);
    if ($jar === '' || !is_array($plan) || !isset($plan['url'])) {
        fwrite(STDERR, "usage: php exam_capture.php <cookie-jar> < plan.json\n");
        return 2;
    }
    $needles = array_map('strval', (array) ($plan['needles'] ?? []));
    $page = fetch(HOST . $plan['url'], $jar, (array) ($plan['headers'] ?? []));
    $assets = collectAssets($page['body'], $jar);

    echo json_encode([
        'url' => $plan['url'],
        'status' => $page['status'],
        'html' => $page['body'],
        'assets' => array_map(static function (array $asset): array {
            return ['url' => $asset['url'], 'bytes' => strlen($asset['body']), 'sha256' => hash('sha256', $asset['body'])];
        }, $assets),
        'hits' => findNeedles($needles, $page['body'], $assets),
    ], JSON_UNESCAPED_UNICODE), "\n";
    return 0;
}

/**
 * 页面引用的同源脚本与样式，逐个取回。
 *
 * @return array<int, array{url: string, body: string}>
 */
function collectAssets(string $html, string $jar): array
{
    $assets = [];
    foreach (assetUrls($html) as $url) {
        if (count($assets) >= MAX_ASSETS) {
            break;
        }
        $absolute = absolute($url);
        if ($absolute === null) {
            continue; // 跨站资源：拿不到本问卷的答案，不抓
        }
        $response = fetch($absolute, $jar);
        $assets[] = ['url' => $url, 'body' => $response['body']];
    }
    return $assets;
}

/**
 * @return string[] 去重后的资源地址，保持出现顺序
 */
function assetUrls(string $html): array
{
    $urls = [];
    if (preg_match_all('/<script[^>]+src=["\']([^"\']+)["\']/i', $html, $scripts) === false) {
        return [];
    }
    preg_match_all('/<link[^>]+href=["\']([^"\']+)["\'][^>]*>/i', $html, $links);
    foreach (array_merge($scripts[1], $links[1]) as $url) {
        $decoded = html_entity_decode($url, ENT_QUOTES | ENT_HTML5, 'UTF-8');
        if (!in_array($decoded, $urls, true)) {
            $urls[] = $decoded;
        }
    }
    return $urls;
}

/**
 * 同源资源的绝对地址；跨站返回 null。
 */
function absolute(string $url): ?string
{
    if (strncmp($url, '//', 2) === 0 || preg_match('#\A[a-z][a-z0-9+.-]*://#i', $url) === 1) {
        $host = parse_url(strncmp($url, '//', 2) === 0 ? 'http:' . $url : $url, PHP_URL_HOST);
        return $host === 'localhost' ? (strncmp($url, '//', 2) === 0 ? 'http:' . $url : $url) : null;
    }
    if ($url === '' || $url[0] === '#' || strncmp($url, 'data:', 5) === 0) {
        return null;
    }
    return HOST . ($url[0] === '/' ? $url : '/' . $url);
}

/**
 * 每个哨兵串出现在哪里。空数组就是"没下发"。
 *
 * @param string[] $needles
 * @param array<int, array{url: string, body: string}> $assets
 * @return array<string, array<int, array{where: string, excerpt: string}>>
 */
function findNeedles(array $needles, string $html, array $assets): array
{
    $hits = [];
    foreach ($needles as $needle) {
        $found = occurrences($needle, 'html', $html);
        foreach ($assets as $asset) {
            $found = array_merge($found, occurrences($needle, $asset['url'], $asset['body']));
        }
        $hits[$needle] = $found;
    }
    return $hits;
}

/**
 * @return array<int, array{where: string, excerpt: string}>
 */
function occurrences(string $needle, string $where, string $haystack): array
{
    if ($needle === '') {
        return [];
    }
    $found = [];
    $offset = 0;
    while (($position = strpos($haystack, $needle, $offset)) !== false) {
        $start = max(0, $position - EXCERPT_RADIUS);
        $found[] = [
            'where' => $where,
            'excerpt' => substr($haystack, $start, strlen($needle) + 2 * EXCERPT_RADIUS),
        ];
        $offset = $position + strlen($needle);
        if (count($found) >= 5) {
            break; // 出现一次就已经是失败了，多抓几条只为让报告好读
        }
    }
    return $found;
}

/**
 * @param string[] $headers
 * @return array{status: int, body: string}
 */
function fetch(string $url, string $jar, array $headers = []): array
{
    $curl = curl_init($url);
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_COOKIEJAR => $jar,
        CURLOPT_COOKIEFILE => $jar,
        CURLOPT_TIMEOUT => HTTP_TIMEOUT_SECONDS,
        CURLOPT_HTTPHEADER => $headers,
    ]);
    $body = curl_exec($curl);
    $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $error = curl_error($curl);
    curl_close($curl);
    if ($body === false || $status >= 500) {
        throw new RuntimeException("抓取 {$url} 失败（状态 {$status}）：{$error}");
    }
    return ['status' => $status, 'body' => (string) $body];
}
