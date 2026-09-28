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
 * Two extra step kinds drive "resume later" (R03-02 "consistent after resuming"),
 * the same engine paths question_slice_support.php uses:
 *   {"save": "<name>"}    post saveall, then the save form with <name> as both
 *                         identifier and password; the half response stays in the table
 *   {"resume": "<name>"}  start a brand new session (fresh cookie jar) and load the
 *                         saved response back with loadall=reload plus name and password
 *
 * Every step — including these two — first records the page currently shown (which
 * questions are on it and whether each is visible or hidden as irrelevant), then acts.
 * A plain step posts that page's form the way a browser would: every hidden field, the
 * current value of every text input, textarea and checked radio, overridden by the
 * step's answers.
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
    // One jar per session: a "resume" step opens a new one, so resuming really does
    // depend on the save name and password rather than on a surviving cookie.
    $jars = [newJar()];
    try {
        $result = runSteps($surveyId, $plan['steps'], $jars);
        echo json_encode($result), "\n";
        return isset($result['broken']) ? 1 : 0;
    } finally {
        foreach ($jars as $jar) {
            @unlink($jar);
        }
    }
}

function newJar(): string
{
    return (string) tempnam(sys_get_temp_dir(), 'logic-e2e-cookies');
}

/**
 * @param array<int, array<string, mixed>> $steps
 * @param array<int, string> $jars
 * @return array{pages: array<int, array<string, mixed>>, completed: bool, broken?: string}
 */
function runSteps(int $surveyId, array $steps, array &$jars): array
{
    $steps = array_values($steps);
    // A plan may open with a resume step. Then do NOT start a session first: the engine
    // creates a response row as soon as a survey is entered, so a throwaway newtest=Y
    // session would leave an empty row behind and make "resuming reuses the same
    // response" unverifiable.
    if (isset($steps[0]['resume'])) {
        $page = loadSaved($surveyId, (string) $steps[0]['resume'], end($jars));
        $steps = array_slice($steps, 1);
    } else {
        $page = httpRequest(BASE_URL . "/$surveyId?lang=en&newtest=Y", null, end($jars));
    }
    $pages = [];
    foreach ($steps as $index => $step) {
        if (strpos($page, COMPLETED_MARKER) !== false) {
            // Every later page was irrelevant: the engine finished early.
            break;
        }
        $document = loadDocument($page);
        $pages[] = describePage($document, $page, $step['needles'] ?? []);
        $next = act($surveyId, $step, $document, $jars);
        if ($next === null) {
            fwrite(STDERR, "step $index: no survey form on the page\n" . substr(strip_tags($page), 0, 600) . "\n");
            return ['pages' => $pages, 'completed' => false, 'broken' => "step $index"];
        }
        $page = $next;
    }
    $pages[] = describePage(loadDocument($page), $page, []);
    return ['pages' => $pages, 'completed' => strpos($page, COMPLETED_MARKER) !== false];
}

/**
 * Carries out one step and returns the page it leads to (null when the page had no form).
 *
 * @param array<string, mixed> $step
 * @param array<int, string> $jars
 */
function act(int $surveyId, array $step, DOMDocument $document, array &$jars): ?string
{
    if (isset($step['save'])) {
        return saveForLater($document, (string) $step['save'], end($jars));
    }
    if (isset($step['resume'])) {
        $jars[] = newJar();
        return loadSaved($surveyId, (string) $step['resume'], end($jars));
    }
    $form = currentForm($document);
    if ($form === null) {
        return null;
    }
    $fields = array_merge($form['fields'], array_map('strval', $step['answers'] ?? []));
    $fields['move'] = (string) ($step['move'] ?? 'movenext');
    return httpRequest($form['action'], $fields, end($jars));
}

/**
 * "Resume later" takes two posts: saveall returns the save form (the engine embeds it in
 * the survey form, carrying a hidden savesubmit), then the name and password are posted.
 */
function saveForLater(DOMDocument $document, string $saveName, string $cookieJar): string
{
    $form = currentForm($document);
    if ($form === null) {
        throw new RuntimeException('no survey form to save from');
    }
    $savePage = httpRequest($form['action'], array_merge($form['fields'], ['saveall' => 'saveall']), $cookieJar);
    $saveForm = currentForm(loadDocument($savePage));
    if ($saveForm === null || !isset($saveForm['fields']['savesubmit'])) {
        throw new RuntimeException("saveall did not return the save form:\n" . substr(strip_tags($savePage), 0, 600));
    }
    return httpRequest($saveForm['action'], array_merge($saveForm['fields'], [
        'savename' => $saveName,
        'savepass' => $saveName,
        'savepass2' => $saveName,
        'saveemail' => '',
    ]), $cookieJar);
}

/** Resume entry point: loadall=reload plus name and password (SurveyIndex.php:456). */
function loadSaved(int $surveyId, string $saveName, string $cookieJar): string
{
    $query = http_build_query([
        'lang' => 'en',
        'loadall' => 'reload',
        'loadname' => $saveName,
        'loadpass' => $saveName,
    ]);
    return httpRequest(BASE_URL . "/$surveyId?$query", null, $cookieJar);
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
