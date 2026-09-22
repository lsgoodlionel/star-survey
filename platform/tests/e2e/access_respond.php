<?php

/**
 * WP-04 access policy e2e: one respondent talking to a published survey over HTTP like a
 * browser, following a scripted plan. Runs inside <prefix>-test-web, driven by
 * platform/tests/e2e/access_policy.py:
 *
 *   php platform/tests/e2e/access_respond.php <cookie-jar-path> < plan.json
 *
 * The cookie jar is a file path, so one "browser" can be resumed across several runs
 * (device identity, unlocked password, a half-filled page) and a fresh path is a fresh browser.
 *
 * Steps (all optional keys):
 *   {"get": "/index.php/123?newtest=Y&lang=en", "headers": ["Date: ..."]}
 *   {"password": "..."}                      post the password page currently shown
 *   {"submit": {"<field>": "value"}, "move": "movesubmit", "extra": {"startdate": "..."}}
 *                                            post the survey form currently shown
 *   {"saveForm": "/tmp/form.json"}           remember the survey form currently shown
 *   {"postSaved": "/tmp/form.json", "move": "movesubmit"}
 *                                            replay a remembered form (e.g. after time ran out)
 *
 * Prints {"pages": [{"kind", "text", "hasCaptcha"}...]} — one entry per response received.
 * kind: password | survey | completed | token | message.
 */

declare(strict_types=1);

const HOST = 'http://localhost';
const HTTP_TIMEOUT_SECONDS = 60;
const TEXT_LIMIT = 600;

exit(main($argv));

function main(array $argv): int
{
    $jar = (string) ($argv[1] ?? '');
    $plan = json_decode((string) stream_get_contents(STDIN), true);
    if ($jar === '' || !is_array($plan) || !isset($plan['steps']) || !is_array($plan['steps'])) {
        fwrite(STDERR, "usage: php access_respond.php <cookie-jar> < plan.json\n");
        return 2;
    }
    $pages = [];
    $html = '';
    foreach ($plan['steps'] as $index => $step) {
        $html = runStep($step, $html, $jar, $index);
        $pages[] = describe($html);
    }
    echo json_encode(['pages' => $pages], JSON_UNESCAPED_UNICODE), "\n";
    return 0;
}

function runStep(array $step, string $html, string $jar, int $index): string
{
    if (isset($step['get'])) {
        return http(HOST . $step['get'], null, $jar, $step['headers'] ?? []);
    }
    if (isset($step['password'])) {
        $form = formById($html, 'mjy-access-password', $index);
        $form['fields']['mjy_access_password'] = (string) $step['password'];
        return http($form['action'], $form['fields'], $jar);
    }
    if (isset($step['saveForm'])) {
        file_put_contents((string) $step['saveForm'], json_encode(formById($html, 'limesurvey', $index)));
        return $html;
    }
    if (isset($step['postSaved'])) {
        $form = json_decode((string) file_get_contents((string) $step['postSaved']), true);
        $fields = array_merge($form['fields'], ['move' => (string) ($step['move'] ?? 'movesubmit')]);
        return http($form['action'], $fields, $jar);
    }
    if (array_key_exists('submit', $step)) {
        $form = formById($html, 'limesurvey', $index);
        $fields = array_merge(
            $form['fields'],
            array_map('strval', (array) $step['submit']),
            array_map('strval', (array) ($step['extra'] ?? [])),
            ['move' => (string) ($step['move'] ?? 'movesubmit')]
        );
        return http($form['action'], $fields, $jar);
    }
    throw new RuntimeException("step $index: unknown step " . json_encode($step));
}

/**
 * @return array{kind: string, text: string, hasCaptcha: bool}
 */
function describe(string $html): array
{
    if (strpos($html, 'id="mjy-access-password"') !== false) {
        $kind = 'password';
    } elseif (strpos($html, 'completed-wrapper') !== false) {
        $kind = 'completed';
    } elseif (strpos($html, 'id="limesurvey"') !== false) {
        $kind = 'survey';
    } elseif (preg_match('/name=["\']token["\']/', $html) === 1) {
        $kind = 'token';
    } else {
        $kind = 'message';
    }
    $text = trim((string) preg_replace('/\s+/u', ' ', strip_tags(preg_replace('#<(script|style)\b.*?</\1>#is', '', $html))));
    return [
        'kind' => $kind,
        'text' => mb_substr($text, 0, TEXT_LIMIT),
        'hasCaptcha' => strpos($html, 'name="loadsecurity"') !== false,
    ];
}

/**
 * @return array{action: string, fields: array<string, string>}
 */
function formById(string $html, string $id, int $index): array
{
    $document = new DOMDocument();
    libxml_use_internal_errors(true);
    $document->loadHTML('<?xml encoding="UTF-8">' . $html);
    libxml_clear_errors();
    $container = $document->getElementById($id);
    if ($container === null) {
        throw new RuntimeException("step $index: no #$id on the page: " . describe($html)['text']);
    }
    $form = $container->nodeName === 'form' ? $container : $container->getElementsByTagName('form')->item(0);
    $fields = [];
    foreach ($form->getElementsByTagName('input') as $input) {
        $name = $input->getAttribute('name');
        $type = strtolower($input->getAttribute('type') ?: 'text');
        if ($name === '' || in_array($type, ['submit', 'button', 'file'], true)) {
            continue;
        }
        if (in_array($type, ['radio', 'checkbox'], true) && !$input->hasAttribute('checked')) {
            continue;
        }
        $fields[$name] = $input->getAttribute('value');
    }
    foreach ($form->getElementsByTagName('textarea') as $textarea) {
        $fields[$textarea->getAttribute('name')] = $textarea->textContent;
    }
    $action = $form->getAttribute('action');
    return ['action' => strpos($action, 'http') === 0 ? $action : HOST . $action, 'fields' => $fields];
}

function http(string $url, ?array $postFields, string $jar, array $headers = []): string
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
    if ($postFields !== null) {
        curl_setopt($curl, CURLOPT_POST, true);
        curl_setopt($curl, CURLOPT_POSTFIELDS, http_build_query($postFields));
    }
    $body = curl_exec($curl);
    $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $error = curl_error($curl);
    curl_close($curl);
    if ($body === false || $status >= 500) {
        throw new RuntimeException("HTTP request to $url failed (status $status): $error");
    }
    return (string) $body;
}
