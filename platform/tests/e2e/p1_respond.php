<?php

/**
 * P1 gate: fills one published survey over HTTP exactly like a respondent's
 * browser (cookies, CSRF token and every hidden field the page carries).
 *
 * Runs inside survey-test-web (see platform/deploy/test/run-p1-e2e.sh):
 *   php platform/tests/e2e/p1_respond.php <sid>
 *
 * Answers every visible question generically: the first option of each radio
 * group, the first checkbox, short text in text inputs and textareas. The
 * "other" and per-option comment texts are left empty: the engine rejects them
 * when their option is not the one chosen.
 * Prints {"completed": true, "pages": n} and exits 0 when the completion page
 * is reached; exits 1 otherwise.
 */

declare(strict_types=1);

const BASE_URL = 'http://localhost/index.php';
const MAX_PAGES = 8;
const COMPLETED_MARKER = 'completed-wrapper'; // the completion page still contains form#limesurvey
const TEXT_ANSWER = 'p1 e2e answer';
const HTTP_TIMEOUT_SECONDS = 60;

exit(main($argv));

function main(array $argv): int
{
    $surveyId = (int) ($argv[1] ?? 0);
    if ($surveyId <= 0) {
        fwrite(STDERR, "usage: php p1_respond.php <sid>\n");
        return 2;
    }
    $cookieJar = tempnam(sys_get_temp_dir(), 'p1-e2e-cookies');
    try {
        $page = httpRequest(BASE_URL . "/$surveyId?lang=en", null, $cookieJar);
        for ($pages = 1; $pages <= MAX_PAGES; $pages++) {
            if (strpos($page, COMPLETED_MARKER) !== false) {
                echo json_encode(['completed' => true, 'pages' => $pages]), "\n";
                return 0;
            }
            $form = parseSurveyForm($page);
            if ($form === null || $form['move'] === null) {
                fwrite(STDERR, "page $pages has no survey form with next/submit:\n" . substr($page, 0, 800) . "\n");
                return 1;
            }
            $page = httpRequest($form['action'], $form['fields'] + ['move' => $form['move']], $cookieJar);
        }
        fwrite(STDERR, "survey $surveyId did not finish within " . MAX_PAGES . " pages\n");
        return 1;
    } finally {
        @unlink($cookieJar);
    }
}

/**
 * @return array{action: string, fields: array<string, string>, move: ?string}|null
 */
function parseSurveyForm(string $html): ?array
{
    $document = new DOMDocument();
    libxml_use_internal_errors(true);
    $document->loadHTML($html);
    libxml_clear_errors();
    $form = $document->getElementById('limesurvey');
    if ($form === null) {
        return null;
    }
    // Hidden fields first: some question types pair an answer control with a
    // hidden input of the same name, and the answer must win.
    $fields = [];
    $answerInputs = [];
    foreach ($form->getElementsByTagName('input') as $input) {
        $name = $input->getAttribute('name');
        if ($name === '') {
            continue;
        }
        if (strtolower($input->getAttribute('type')) === 'hidden') {
            $fields[$name] = $input->getAttribute('value');
        } else {
            $answerInputs[] = $input;
        }
    }
    $radioGroups = [];
    $hasCheckedBox = false;
    foreach ($answerInputs as $input) {
        $name = $input->getAttribute('name');
        $type = strtolower($input->getAttribute('type') ?: 'text');
        $value = $input->getAttribute('value');
        if ($type === 'radio' && !isset($radioGroups[$name]) && $value !== '') {
            $radioGroups[$name] = true;
            $fields[$name] = $value;
        } elseif ($type === 'checkbox' && !$hasCheckedBox) {
            $hasCheckedBox = true;
            $fields[$name] = $value === '' ? 'Y' : $value;
        } elseif ($type === 'text' && !isFollowUpText($name)) {
            $fields[$name] = TEXT_ANSWER;
        }
    }
    foreach ($form->getElementsByTagName('textarea') as $textarea) {
        $fields[$textarea->getAttribute('name')] = TEXT_ANSWER;
    }
    $moves = [];
    foreach ($form->getElementsByTagName('button') as $button) {
        if ($button->getAttribute('name') === 'move') {
            $moves[] = $button->getAttribute('value');
        }
    }
    $move = in_array('movesubmit', $moves, true) ? 'movesubmit' : (in_array('movenext', $moves, true) ? 'movenext' : null);
    $action = $form->getAttribute('action');
    if (strpos($action, 'http') !== 0) {
        $action = 'http://localhost' . $action;
    }
    return ['action' => $action, 'fields' => $fields, 'move' => $move];
}

/** Free text that belongs to an option ("other", per-option comment), not to the question. */
function isFollowUpText(string $name): bool
{
    return preg_match('/(other|comment)$/i', $name) === 1;
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
