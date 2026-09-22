<?php

/**
 * WP-03 logic e2e: the engine's own ExpressionManager parses every expression the
 * gateway compiled into a published survey.
 *
 * Runs inside survey-test-web (driven by platform/tests/e2e/publish_gateway_logic.py):
 *   php platform/tests/e2e/logic_engine_check.php <sid>
 *
 * Loads the survey into LimeExpressionManager (StartSurvey registers every qcode
 * variable), then evaluates, parse-only, each group relevance, question relevance,
 * em_validation_q / em_validation_q_tip / equation attribute and every {...}
 * substitution in question, help, answer and subquestion texts. Any syntax error or
 * unknown variable is reported.
 *
 * Prints {"checked": n, "errors": [{"where": ..., "expression": ..., "errors": [...]}]}.
 */

declare(strict_types=1);

require_once __DIR__ . '/question_slice_support.php';

const EXPRESSION_ATTRIBUTES = ['em_validation_q', 'em_validation_q_tip', 'equation'];
const TEMPLATE_ATTRIBUTES = ['em_validation_q_tip', 'equation'];

exit(main($argv));

function main(array $argv): int
{
    $surveyId = (int) ($argv[1] ?? 0);
    if ($surveyId <= 0) {
        fwrite(STDERR, "usage: php logic_engine_check.php <sid>\n");
        return 2;
    }
    bootstrapEngine();
    Yii::import('application.helpers.expressions.em_manager_helper', true);
    Yii::import('application.helpers.replacements_helper', true);
    Yii::app()->loadHelper('surveytranslator');
    $db = connect();
    Yii::app()->session->open();
    LimeExpressionManager::SetSurveyId($surveyId);
    $_SESSION['LEMlang'] = 'en';
    $_SESSION['LEMsid'] = $surveyId;
    LimeExpressionManager::StartSurvey($surveyId, 'survey', ['active' => true], true);
    // Test-only: the ExpressionManager that StartSurvey filled with this survey's variables.
    $property = new ReflectionProperty(LimeExpressionManager::class, 'em');
    $property->setAccessible(true);
    $em = $property->getValue(LimeExpressionManager::singleton());

    $checked = 0;
    $errors = [];
    foreach (collectExpressions($db, $surveyId) as [$where, $expression, $isTemplate]) {
        $parts = $isTemplate ? substitutions($em, $expression) : [$expression];
        foreach ($parts as $part) {
            $checked++;
            $em->RDP_Evaluate($part, true);
            if ($em->HasErrors()) {
                $errors[] = ['where' => $where, 'expression' => $part, 'errors' => errorMessages($em)];
            }
        }
    }
    // Negative control: an unknown variable and a syntax error must be caught, or
    // "no errors" above would prove nothing.
    $canaries = [];
    foreach (['QNOSUCHQUESTION.NAOK > 1', 'QAGE.NAOK >'] as $canary) {
        $em->RDP_Evaluate($canary, true);
        $canaries[$canary] = $em->HasErrors();
    }
    echo json_encode(['checked' => $checked, 'errors' => $errors, 'canariesCaught' => $canaries], JSON_UNESCAPED_UNICODE), "\n";
    return 0;
}

/** @return string[] the inner text of every {...} substitution the engine would evaluate */
function substitutions(ExpressionManager $em, string $text): array
{
    $parts = [];
    foreach ($em->asSplitStringOnExpressions($text) as $token) {
        if ($token[2] === 'EXPRESSION') {
            $parts[] = substr((string) $token[0], 1, -1);
        }
    }
    return $parts;
}

/** @return string[] */
function errorMessages(ExpressionManager $em): array
{
    return array_map(static function ($error): string {
        return is_array($error) ? (string) $error[0] . (isset($error[1][0]) ? ' at ' . $error[1][0] : '') : (string) $error;
    }, $em->RDP_GetErrors());
}

/** @return array<int, array{0: string, 1: string, 2: bool}> */
function collectExpressions(PDO $db, int $surveyId): array
{
    $found = [];
    foreach (rows($db, 'SELECT gid, grelevance FROM ' . table('groups') . ' WHERE sid = ?', [$surveyId]) as $row) {
        $found[] = ["group {$row['gid']} relevance", (string) $row['grelevance'], false];
    }
    $questions = rows($db, 'SELECT qid, title, relevance FROM ' . table('questions') . ' WHERE sid = ?', [$surveyId]);
    foreach ($questions as $row) {
        $found[] = ["{$row['title']} relevance", (string) $row['relevance'], false];
    }
    $qids = array_column($questions, 'title', 'qid');
    $in = implode(',', array_map('intval', array_keys($qids)));
    $attributes = rows($db, 'SELECT qid, attribute, value FROM ' . table('question_attributes')
        . " WHERE qid IN ($in) AND attribute IN ('" . implode("','", EXPRESSION_ATTRIBUTES) . "')", []);
    foreach ($attributes as $row) {
        $isTemplate = in_array($row['attribute'], TEMPLATE_ATTRIBUTES, true);
        $found[] = ["{$qids[$row['qid']]} {$row['attribute']}", (string) $row['value'], $isTemplate];
    }
    foreach (rows($db, 'SELECT qid, question, help FROM ' . table('question_l10ns') . " WHERE qid IN ($in)", []) as $row) {
        $found[] = ["{$qids[$row['qid']]} text", (string) $row['question'], true];
        $found[] = ["{$qids[$row['qid']]} help", (string) $row['help'], true];
    }
    $answers = rows($db, 'SELECT a.qid, l.answer FROM ' . table('answers') . ' a JOIN ' . table('answer_l10ns')
        . " l ON l.aid = a.aid WHERE a.qid IN ($in)", []);
    foreach ($answers as $row) {
        $found[] = ["{$qids[$row['qid']]} answer", (string) $row['answer'], true];
    }
    return array_values(array_filter($found, static function (array $item): bool {
        return trim($item[1]) !== '' && ($item[2] || trim($item[1]) !== '1');
    }));
}

function rows(PDO $db, string $sql, array $params): array
{
    $statement = $db->prepare($sql);
    $statement->execute($params);
    return $statement->fetchAll(PDO::FETCH_ASSOC);
}
