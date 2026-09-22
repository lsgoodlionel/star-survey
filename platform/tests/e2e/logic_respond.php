<?php

/**
 * WP-03 logic e2e: fills one published survey over HTTP like a browser, following
 * a scripted plan, and reports what each page showed.
 *
 * Runs inside survey-test-web (driven by platform/tests/e2e/publish_gateway_logic.py):
 *   php platform/tests/e2e/logic_respond.php <sid> < plan.json
 *
 * plan.json: {"steps": [{"answers": {"<fieldname>": "<value>"}, "move": "movenext|moveprev|movesubmit",
 *                        "needles": ["text to look for in the page shown before this step"]}]}
 *
 * Every step first records the page currently shown (which questions are on it and
 * whether each is visible or hidden as irrelevant), then posts that page's form the
 * way a browser would: every hidden field, the current value of every text input,
 * textarea and checked radio, overridden by the step's answers.
 *
 * Prints {"pages": [...], "completed": bool} as JSON on stdout.
 */

declare(strict_types=1);

const BASE_URL = 'http://localhost/index.php';
const COMPLETED_MARKER = 'completed-wrapper';
const HTTP_TIMEOUT_SECONDS = 60;
const QUESTION_ID_PREFIX = 'question';
const IRRELEVANT_CLASS = 'ls-irrelevant';

exit(main($argv));

function main(array $argv): int
{
    $surveyId = (int) ($argv[1] ?? 0);
    $plan = json_decode((string) stream_get_contents(STDIN), true);
    if ($surveyId <= 0 || !is_array($plan) || !isset($plan['steps']) || !is_array($plan['steps'])) {
        fwrite(STDERR, "usage: php logic_respond.php <sid> < plan.json\n");
        return 2;
    }
    $cookieJar = tempnam(sys_get_temp_dir(), 'logic-e2e-cookies');
    try {
        $page = httpRequest(BASE_URL . "/$surveyId?lang=en&newtest=Y", null, $cookieJar);
        $pages = [];
        foreach ($plan['steps'] as $index => $step) {
            if (strpos($page, COMPLETED_MARKER) !== false) {
                // Every later page was irrelevant: the engine finished early.
                break;
            }
            $document = loadDocument($page);
            $pages[] = describePage($document, $page, $step['needles'] ?? []);
            $form = currentForm($document);
            if ($form === null) {
                fwrite(STDERR, "step $index: no survey form on the page\n" . substr(strip_tags($page), 0, 600) . "\n");
                echo json_encode(['pages' => $pages, 'completed' => false]), "\n";
                return 1;
            }
            $fields = array_merge($form['fields'], array_map('strval', $step['answers'] ?? []));
            $fields['move'] = (string) ($step['move'] ?? 'movenext');
            $page = httpRequest($form['action'], $fields, $cookieJar);
        }
        $pages[] = describePage(loadDocument($page), $page, []);
        echo json_encode(['pages' => $pages, 'completed' => strpos($page, COMPLETED_MARKER) !== false]), "\n";
        return 0;
    } finally {
        @unlink($cookieJar);
    }
}

function loadDocument(string $html): DOMDocument
{
    $document = new DOMDocument();
    libxml_use_internal_errors(true);
    $document->loadHTML('<?xml encoding="UTF-8">' . $html);
    libxml_clear_errors();
    return $document;
}

/**
 * @return array{questions: array<string, string>, needles: array<string, bool>}
 */
function describePage(DOMDocument $document, string $html, array $needles): array
{
    $questions = [];
    foreach ($document->getElementsByTagName('div') as $div) {
        $id = $div->getAttribute('id');
        if (preg_match('/^' . QUESTION_ID_PREFIX . '(\d+)$/', $id, $match) !== 1) {
            continue;
        }
        $classes = preg_split('/\s+/', $div->getAttribute('class')) ?: [];
        $questions[$match[1]] = in_array(IRRELEVANT_CLASS, $classes, true) ? 'hidden' : 'visible';
    }
    $found = [];
    foreach ($needles as $needle) {
        $found[$needle] = strpos($html, $needle) !== false;
    }
    return ['questions' => (object) $questions, 'needles' => (object) $found];
}

/**
 * @return array{action: string, fields: array<string, string>}|null
 */
function currentForm(DOMDocument $document): ?array
{
    $form = $document->getElementById('limesurvey');
    if ($form === null) {
        return null;
    }
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
    if (strpos($action, 'http') !== 0) {
        $action = 'http://localhost' . $action;
    }
    return ['action' => $action, 'fields' => $fields];
}

function httpRequest(string $url, ?array $postFields, string $cookieJar): string
{
    $curl = curl_init($url);
    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_COOKIEJAR => $cookieJar,
        CURLOPT_COOKIEFILE => $cookieJar,
        CURLOPT_TIMEOUT => HTTP_TIMEOUT_SECONDS,
    ]);
    if ($postFields !== null) {
        curl_setopt($curl, CURLOPT_POST, true);
        curl_setopt($curl, CURLOPT_POSTFIELDS, http_build_query($postFields));
    }
    $body = curl_exec($curl);
    $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $error = curl_error($curl);
    curl_close($curl);
    if ($body === false || $status >= 400) {
        throw new RuntimeException("HTTP request to $url failed (status $status): $error");
    }
    return (string) $body;
}
