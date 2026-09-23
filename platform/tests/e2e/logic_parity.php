<?php

/**
 * WP-03.4 双执行比对的**引擎那一次执行**。
 *
 * 在 <prefix>-test-web 里运行，由 platform/tests/e2e/publish_gateway_parity.py 驱动：
 *   php platform/tests/e2e/logic_parity.php <sid> < plan.json
 *
 * 用的是引擎自己的 ExpressionManager（不是仿制品）：StartSurvey 把这份问卷的全部
 * qcode 变量注册进来之后，逐条用例把答案写进 $_SESSION[responses_<sid>]，再让
 * RDP_Evaluate 求值网关编译出来的 ExpressionScript。
 *
 * 关于 relevanceStatus：_GetVarAttribute 读 .NAOK 时，题目的 relevanceStatus 缺省是
 * **0**（假），值会被读成空串。所以每条用例都要显式把所有题置为「相关」，再把
 * 本次要隐藏的题置 0——这正好就是契约里的「隐藏即空」。
 *
 * plan: {"cases":[{"id":..., "expression":..., "answers":{qcode:值}, "hidden":[题目代码]}]}
 * 输出: {"results":[{"id","value","errors":[]}], "canariesCaught":{...}, "unknownVariables":[...]}
 */

declare(strict_types=1);

require_once __DIR__ . '/question_slice_support.php';

exit(main($argv));

function main(array $argv): int
{
    $surveyId = (int) ($argv[1] ?? 0);
    if ($surveyId <= 0) {
        fwrite(STDERR, "usage: php logic_parity.php <sid> < plan.json\n");
        return 2;
    }
    $plan = json_decode((string) file_get_contents('php://stdin'), true);
    if (!is_array($plan) || !isset($plan['cases']) || !is_array($plan['cases'])) {
        fwrite(STDERR, "plan must be {\"cases\": [...]}\n");
        return 2;
    }

    $engine = startEngine($surveyId);
    $results = [];
    $unknown = [];
    foreach ($plan['cases'] as $case) {
        $results[] = evaluateCase($engine, $case, $unknown);
    }

    echo json_encode([
        'results' => $results,
        // 反向对照：比对机制本身必须能报错，否则「全对」什么也证明不了。
        'canariesCaught' => canaries($engine),
        'unknownVariables' => array_values(array_unique($unknown)),
    ], JSON_UNESCAPED_UNICODE), "\n";
    return 0;
}

/**
 * 载入问卷并取出引擎内部的 ExpressionManager 与 qcode → sgqa 映射。
 * 这些成员是 private，测试里用反射读——和 logic_engine_check.php 的做法一致。
 *
 * @return array{em: ExpressionManager, sessid: string, sgqa: array<string,string>, qids: array<string,int>, groups: int}
 */
function startEngine(int $surveyId): array
{
    bootstrapEngine();
    Yii::import('application.helpers.expressions.em_manager_helper', true);
    Yii::app()->loadHelper('surveytranslator');
    $db = connect();
    Yii::app()->session->open();
    LimeExpressionManager::SetSurveyId($surveyId);
    $_SESSION['LEMlang'] = 'en';
    $_SESSION['LEMsid'] = $surveyId;
    LimeExpressionManager::StartSurvey($surveyId, 'survey', ['active' => true], true);
    $lem = LimeExpressionManager::singleton();

    $qids = [];
    foreach (query($db, 'SELECT qid, title FROM ' . table('questions') . " WHERE sid = $surveyId AND parent_qid = 0") as $row) {
        $qids[(string) $row['title']] = (int) $row['qid'];
    }
    $groups = query($db, 'SELECT COUNT(*) AS n FROM ' . table('groups') . " WHERE sid = $surveyId");

    return [
        'em' => readPrivate($lem, 'em'),
        'sessid' => readPrivate($lem, 'sessid'),
        'sgqa' => readPrivate($lem, 'qcode2sgqa'),
        'qids' => $qids,
        'groups' => (int) $groups[0]['n'],
    ];
}

/** @return mixed */
function readPrivate(LimeExpressionManager $lem, string $name)
{
    $property = new ReflectionProperty(LimeExpressionManager::class, $name);
    $property->setAccessible(true);
    return $property->getValue($lem);
}

/**
 * 一条用例：清空答卷会话，注入这一份答案与隐藏状态，再求值。
 *
 * @param array{em: ExpressionManager, sessid: string, sgqa: array, qids: array, groups: int} $engine
 * @param array<string, mixed> $case
 * @param string[] $unknown
 * @return array<string, mixed>
 */
function evaluateCase(array $engine, array $case, array &$unknown): array
{
    $errors = [];
    applyAnswers($engine, (array) ($case['answers'] ?? []), (array) ($case['hidden'] ?? []), $errors, $unknown);

    $em = $engine['em'];
    $em->RDP_Evaluate((string) $case['expression']);
    foreach (errorMessages($em) as $message) {
        $errors[] = $message;
    }
    return [
        'id' => (string) $case['id'],
        'value' => $errors ? '' : renderValue($em->GetResult()),
        'errors' => $errors,
    ];
}

/**
 * @param array{sessid: string, sgqa: array, qids: array, groups: int} $engine
 * @param array<string, mixed> $answers
 * @param string[] $hidden
 * @param string[] $errors
 * @param string[] $unknown
 */
function applyAnswers(array $engine, array $answers, array $hidden, array &$errors, array &$unknown): void
{
    $sessid = $engine['sessid'];
    $_SESSION[$sessid] = [];

    // 缺省全部「相关」：引擎里题目 relevanceStatus 的缺省值是 0，不置位的话每个引用都读成空。
    $status = [];
    foreach ($engine['qids'] as $qid) {
        $status[$qid] = 1;
    }
    for ($gseq = 0; $gseq < $engine['groups']; $gseq++) {
        $status['G' . $gseq] = 1;
    }
    foreach ($hidden as $code) {
        if (!isset($engine['qids'][$code])) {
            $errors[] = "hidden question {$code} does not exist in the published survey";
            continue;
        }
        $status[$engine['qids'][$code]] = 0;
    }
    $_SESSION[$sessid]['relevanceStatus'] = $status;

    foreach ($answers as $qcode => $value) {
        if (!isset($engine['sgqa'][$qcode])) {
            $unknown[] = (string) $qcode;
            $errors[] = "no engine variable named {$qcode}";
            continue;
        }
        $_SESSION[$sessid][$engine['sgqa'][$qcode]] = (string) $value;
    }
}

/**
 * 和平台侧 pubgw/logic/evaluate.py 的 render() 逐字对齐：
 * 空 → ""；真 → "1"，假 → ""；数字定点十位去尾零；其余原样。
 * @param mixed $value
 */
function renderValue($value): string
{
    if ($value === null) {
        return '';
    }
    if (is_bool($value)) {
        return $value ? '1' : '';
    }
    if (is_int($value) || is_float($value)) {
        $number = (float) $value;
        if (is_nan($number) || is_infinite($number)) {
            return 'NAN';
        }
        $text = rtrim(rtrim(sprintf('%.10F', $number), '0'), '.');
        return ($text === '' || $text === '-') ? '0' : $text;
    }
    return (string) $value;
}

/**
 * 反向对照：一个不存在的变量、一处语法错误、一个算错的值，都必须被抓住。
 * @param array{em: ExpressionManager} $engine
 * @return array<string, bool>
 */
function canaries(array $engine): array
{
    $em = $engine['em'];
    $caught = [];
    foreach (['QNOSUCHQUESTION.NAOK > 1', '1 +'] as $canary) {
        $em->RDP_Evaluate($canary);
        $caught[$canary] = $em->HasErrors();
    }
    // 求值确实在跑：1+1 必须是 2，而不是任何「反正相等」的假象。
    $em->RDP_Evaluate('sum(1, 1)');
    $caught['sum(1, 1) == 2'] = !$em->HasErrors() && renderValue($em->GetResult()) === '2';
    return $caught;
}

/** @return string[] */
function errorMessages(ExpressionManager $em): array
{
    if (!$em->HasErrors()) {
        return [];
    }
    return array_map(static function ($error): string {
        return is_array($error) ? (string) $error[0] : (string) $error;
    }, $em->RDP_GetErrors());
}

/** @return array<int, array<string, mixed>> */
function query(PDO $db, string $sql): array
{
    $statement = $db->query($sql);
    return $statement === false ? [] : $statement->fetchAll(PDO::FETCH_ASSOC);
}
