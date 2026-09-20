<?php

/**
 * P0-00.3 题型纵切端到端：三种题型走完整生命周期。
 *
 * 在 survey-test-web 内运行（见 run-question-slice.sh）。链路与真实发布一致：
 *   .lss → import_survey → activate_survey → 真实 HTTP 作答 → 断点续答
 *   → 已提交答卷的修改 → 导出（引擎导出 ＋ 主表与副表合并导出）→ 重新导入/主题升级。
 *
 * 三条纵切：
 *   1. QMATRIX  引擎原生数组题（type F），不加任何扩展；
 *   2. QTABLE   自增表格（长文本 ＋ 题型主题 mjy-repeating-table ＋ 结构化副表）；
 *   3. QUPLOAD  文件上传题（type |）＋ 平台侧上传会话。
 *
 * 全部场景通过时退出码为 0。
 */

declare(strict_types=1);

require_once __DIR__ . '/question_slice_support.php';

/** 作答者填进自增表格的内容；列顺序刻意打乱，用来验证服务端归一化。 */
const TABLE_ANSWER_SCRAMBLED = '{"v":1,"rows":[{"qty":"2","item":"  录音笔  ","note":"备用"},{"note":"","qty":"1","item":"三脚架"}]}';
const TABLE_ANSWER_NORMALISED = '{"v":1,"rows":[{"item":"录音笔","qty":"2","note":"备用"},{"item":"三脚架","qty":"1","note":""}]}';
/** 违反列规格（qty 不是整数、item 为空）：服务端必须拒收。 */
const TABLE_ANSWER_INVALID = '{"v":1,"rows":[{"item":"","qty":"abc"}]}';
/** 结构闸门 preg 之外的垃圾：连信封都不是。 */
const TABLE_ANSWER_NOT_JSON = 'not json at all';

main();

function main(): void
{
    bootstrapEngine();
    $db = connect();
    enableRemoteControl($db);
    enablePlugin($db, BRIDGE_PLUGIN, 0);
    enablePlugin($db, QUESTIONS_PLUGIN, 10);
    installQuestionTheme();

    $key = login();
    try {
        $surveyId = publish($key);
        info("survey $surveyId published and activated");
        $context = ['db' => $db, 'key' => $key, 'surveyId' => $surveyId, 'map' => questionMap($db, $surveyId)];

        $results = [
            scenarioPublish($context),
            scenarioRespondentFillsEveryType($context),
            scenarioServerSideRejection($context),
            scenarioResumePartialResponse($context),
            scenarioEditSubmittedResponse($context),
            scenarioExports($context),
            scenarioThemeUpgradeAndReimport($context),
        ];
    } finally {
        rpc('release_session_key', [$key]);
    }

    $failed = array_filter($results, static function (array $result): bool {
        return !$result['passed'];
    });
    echo json_encode(
        ['driver' => Yii::app()->db->getDriverName(), 'scenarios' => $results],
        JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
    ), "\n";
    exit(count($failed) === 0 ? 0 : 1);
}

// ------------------------------------------------------------------- 场景一

/**
 * 发布：.lss 里带的题型主题名、插件属性、结构闸门必须原样落地。
 *
 * @param array<string, mixed> $context
 */
function scenarioPublish(array $context): array
{
    $db = $context['db'];
    $surveyId = $context['surveyId'];
    $map = $context['map'];
    $columns = responseColumns($db, $surveyId);

    $tableRow = questionRow($db, $surveyId, 'QTABLE');
    $checks = [
        '数组题子题各占一列' => count(array_intersect([
            $map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ001'],
            $map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ002'],
            $map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ003'],
        ], $columns)) === 3,
        '自增表格只占一列' => in_array($map['QTABLE']['field'], $columns, true),
        '上传题占值列与计数列两列' => in_array($map['QUPLOAD']['field'], $columns, true)
            && in_array($map['QUPLOAD']['field'] . '_Cfilecount', $columns, true),
        '题型主题名随 .lss 落地' => $tableRow['question_theme_name'] === THEME_NAME,
        '插件属性随 .lss 落地' => questionAttribute($db, (int) $tableRow['qid'], 'mjy_table_columns') !== null,
        '列定义 JSON 未被 XSS 过滤改写' => json_decode(
            (string) questionAttribute($db, (int) $tableRow['qid'], 'mjy_table_columns'),
            true
        ) !== null,
        '结构闸门 preg 随 .lss 落地' => strpos((string) $tableRow['preg'], 'rows') !== false,
        // 引擎缺口：题型主题的 answercolumndefinition 在 7.1.2 里是死代码，
        // createFieldMap 的守卫判断的是查询里根本不存在的 $arow['attribute']
        // （application/helpers/common_helper.php:1748），列类型永远是默认的 text。
        '题型主题无法提升答卷列类型（引擎缺口）' => responseColumnType($db, $surveyId, $map['QTABLE']['field']) === 'text',
    ];
    return result('发布：三种题型的列与元数据', $checks);
}

// ------------------------------------------------------------------- 场景二

/**
 * 作答者用真实 HTTP 把三道题都填完并提交。
 *
 * @param array<string, mixed> $context
 */
function scenarioRespondentFillsEveryType(array $context): array
{
    $db = $context['db'];
    $surveyId = $context['surveyId'];
    $map = $context['map'];

    $session = startResponse();
    $outcome = walkSurvey($session, $surveyId, $map, [
        'matrix' => ['SQ001' => 'A1', 'SQ002' => 'A3', 'SQ003' => 'A4'],
        'table' => TABLE_ANSWER_SCRAMBLED,
        'upload' => true,
    ]);
    closeSession($session);

    $responseId = latestResponseId($db, $surveyId);
    $row = responseRow($db, $surveyId, $responseId);
    $files = json_decode((string) $row[$map['QUPLOAD']['field']], true) ?: [];
    $sessions = uploadSessions($db, $surveyId, $responseId, 'QUPLOAD');

    $checks = [
        '提交成功' => $outcome['completed'],
        '数组题三个子题各自入列' => [
            $row[$map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ001']],
            $row[$map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ002']],
            $row[$map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ003']],
        ] === ['A1', 'A3', 'A4'],
        '自增表格被服务端归一化后入库' => $row[$map['QTABLE']['field']] === TABLE_ANSWER_NORMALISED,
        '副表还原出两行' => sideTableRows($db, $surveyId, $responseId, 'QTABLE') === [
            ['item' => '录音笔', 'qty' => '2', 'note' => '备用'],
            ['item' => '三脚架', 'qty' => '1', 'note' => ''],
        ],
        '副表状态为通过' => (int) sideTableState($db, $surveyId, $responseId, 'QTABLE')['is_valid'] === 1,
        '上传题存下最终文件名' => count($files) === 1 && strpos((string) $files[0]['filename'], 'fu_') === 0,
        '上传计数列同步' => (int) $row[$map['QUPLOAD']['field'] . '_Cfilecount'] === 1,
        '上传会话已回绑最终文件名' => count($sessions) === 1
            && $sessions[0]['state'] === 'bound'
            && $sessions[0]['stored_name'] === $files[0]['filename'],
        '上传会话开出来时临时名与最终名不同' => count($sessions) === 1
            && $sessions[0]['temp_name'] !== $sessions[0]['stored_name'],
    ];
    $outcomeResult = result('作答：三种题型的真实 HTTP 填写', $checks);
    $outcomeResult['responseId'] = $responseId;
    return $outcomeResult;
}

// ------------------------------------------------------------------- 场景三

/**
 * 服务端拒收：两条闸门各挡一类垃圾。
 *  - preg（引擎原生，EM 在服务端求值）挡连信封都不是的内容；
 *  - 插件 beforeSurveyPage 挡信封对但违反列规格的内容。
 *
 * @param array<string, mixed> $context
 */
function scenarioServerSideRejection(array $context): array
{
    $db = $context['db'];
    $surveyId = $context['surveyId'];
    $map = $context['map'];

    $notJson = attemptTableAnswer($surveyId, $map, TABLE_ANSWER_NOT_JSON);
    $invalid = attemptTableAnswer($surveyId, $map, TABLE_ANSWER_INVALID);
    $valid = attemptTableAnswer($surveyId, $map, TABLE_ANSWER_SCRAMBLED);

    $responseId = latestResponseId($db, $surveyId);
    $checks = [
        'preg 挡住非信封内容（停留在本页）' => !$notJson['advanced'],
        '插件挡住违反列规格的内容（停留在本页）' => !$invalid['advanced'],
        '插件把拒收理由显示出来' => strpos($invalid['body'], '不能为空') !== false
            || strpos($invalid['body'], '必须是整数') !== false,
        '合法内容可以前进' => $valid['advanced'],
        '被拒收的内容没有进副表' => sideTableRows($db, $surveyId, $responseId, 'QTABLE') !== [
            ['item' => '', 'qty' => 'abc', 'note' => ''],
        ],
    ];
    return result('服务端拒收：两条闸门', $checks);
}

// ------------------------------------------------------------------- 场景四

/**
 * 断点续答：填完第一页「稍后继续」，再用同一账号加载回来接着填。
 *
 * @param array<string, mixed> $context
 */
function scenarioResumePartialResponse(array $context): array
{
    $db = $context['db'];
    $surveyId = $context['surveyId'];
    $map = $context['map'];
    $saveName = 'resume' . random_int(100000, 999999);

    $first = startResponse();
    $page = openSurvey($first, $surveyId);
    $page = submitPage($first, $page, fillFields($page, $map, ['matrix' => ['SQ001' => 'A2']]), 'movenext');
    saveAndResumeLater($first, $page, $saveName);
    closeSession($first);
    $savedControl = savedControlRow($db, $surveyId, $saveName);
    $savedResponseId = latestResponseId($db, $surveyId);
    $savedRow = responseRow($db, $surveyId, $savedResponseId);

    $second = startResponse();
    $loaded = loadSavedResponse($second, $surveyId, $saveName);
    $outcome = continueSurvey($second, $loaded, $map, [
        'table' => TABLE_ANSWER_SCRAMBLED,
        'upload' => false,
    ]);
    closeSession($second);

    $resumedId = latestResponseId($db, $surveyId);
    $resumedRow = responseRow($db, $surveyId, $resumedId);
    $checks = [
        '「稍后继续」写出断点记录' => $savedControl !== null
            && (int) $savedControl['srid'] === $savedResponseId,
        '半份答卷已经落库且未完成' => $savedRow['submitdate'] === null
            && $savedRow[$map['QMATRIX']['field'] . '_S' . $map['QMATRIX']['subquestions']['SQ001']] === 'A2',
        '续答复用同一条答卷' => $resumedId === $savedResponseId,
        '续答后提交成功' => $outcome['completed'],
        '第一页答案在续答后仍在' => $resumedRow[$map['QMATRIX']['field'] . '_S'
            . $map['QMATRIX']['subquestions']['SQ001']] === 'A2',
        '续答阶段填的自增表格进了副表' => sideTableRows($db, $surveyId, $resumedId, 'QTABLE') === [
            ['item' => '录音笔', 'qty' => '2', 'note' => '备用'],
            ['item' => '三脚架', 'qty' => '1', 'note' => ''],
        ],
    ];
    $result = result('断点续答', $checks);
    $result['responseId'] = $resumedId;
    return $result;
}

// ------------------------------------------------------------------- 场景五

/**
 * 修改已提交的答卷：RemoteControl update_response 走 SurveyDynamic::encryptSave()，
 * 会触发 afterSurveyDynamicSave，但**完全绕过 EM**：preg、必答、插件闸门都不生效。
 *
 * @param array<string, mixed> $context
 */
function scenarioEditSubmittedResponse(array $context): array
{
    $db = $context['db'];
    $key = $context['key'];
    $surveyId = $context['surveyId'];
    $map = $context['map'];

    $session = startResponse();
    walkSurvey($session, $surveyId, $map, [
        'matrix' => ['SQ001' => 'A1'],
        'table' => TABLE_ANSWER_SCRAMBLED,
        'upload' => false,
    ]);
    closeSession($session);
    $responseId = latestResponseId($db, $surveyId);

    $editedAnswer = '{"v":1,"rows":[{"item":"改过的设备","qty":"9","note":"管理员改的"}]}';
    $edited = rpc('update_response', [$key, $surveyId, [
        'id' => $responseId,
        $map['QTABLE']['field'] => $editedAnswer,
    ]]);
    $rowsAfterEdit = sideTableRows($db, $surveyId, $responseId, 'QTABLE');

    $junk = rpc('update_response', [$key, $surveyId, [
        'id' => $responseId,
        $map['QTABLE']['field'] => TABLE_ANSWER_NOT_JSON,
    ]]);
    $stateAfterJunk = sideTableState($db, $surveyId, $responseId, 'QTABLE');

    $checks = [
        'update_response 被接受' => $edited === true,
        '副表跟着改动更新' => $rowsAfterEdit === [['item' => '改过的设备', 'qty' => '9', 'note' => '管理员改的']],
        'update_response 不跑任何校验（引擎缺口）' => $junk === true
            && responseRow($db, $surveyId, $responseId)[$map['QTABLE']['field']] === TABLE_ANSWER_NOT_JSON,
        '副表把这条标记为不可信' => (int) $stateAfterJunk['is_valid'] === 0
            && $stateAfterJunk['errors'] !== '',
        '被标记不可信时副表不留行' => sideTableRows($db, $surveyId, $responseId, 'QTABLE') === [],
    ];
    return result('修改已提交的答卷', $checks);
}

// ------------------------------------------------------------------- 场景六

/**
 * 导出：引擎自己的导出只看主表，自增表格是一整块 JSON；
 * 平台要的逐行视图必须靠主表与副表合并。
 *
 * @param array<string, mixed> $context
 */
function scenarioExports(array $context): array
{
    $db = $context['db'];
    $key = $context['key'];
    $surveyId = $context['surveyId'];
    $map = $context['map'];

    $session = startResponse();
    walkSurvey($session, $surveyId, $map, [
        'matrix' => ['SQ001' => 'A2', 'SQ002' => 'A2'],
        'table' => TABLE_ANSWER_SCRAMBLED,
        'upload' => true,
    ]);
    closeSession($session);
    $responseId = latestResponseId($db, $surveyId);

    $csv = base64_decode((string) rpc('export_responses', [$key, $surveyId, 'csv', 'en', 'all', 'code', 'short']));
    $combined = combinedExport($db, $surveyId, $responseId);
    $uploaded = rpc('get_uploaded_files', [$key, $surveyId, null, $responseId]);

    $checks = [
        '引擎导出包含数组题的每个子题列' => strpos($csv, 'QMATRIX[SQ001]') !== false,
        '引擎导出把自增表格当成一整块 JSON' => strpos($csv, '"v":1') !== false
            || strpos($csv, '""v"":1') !== false,
        '引擎导出没有逐行列' => strpos($csv, 'QTABLE[1][item]') === false,
        '合并导出给出逐行逐列' => $combined['QTABLE'] === [
            ['item' => '录音笔', 'qty' => '2', 'note' => '备用'],
            ['item' => '三脚架', 'qty' => '1', 'note' => ''],
        ],
        '合并导出带上上传资产 id' => count($combined['uploads']) === 1
            && preg_match('/^[0-9a-f-]{36}$/', $combined['uploads'][0]['upload_token']) === 1,
        'get_uploaded_files 只给引擎文件名，没有平台资产 id' => is_array($uploaded)
            && count($uploaded) === 1
            && !isset(reset($uploaded)['upload_token']),
    ];
    return result('导出：引擎导出与主副表合并导出', $checks);
}

// ------------------------------------------------------------------- 场景七

/**
 * 主题升级与重新导入：
 *  - 重装（升级）题型主题后，已有答卷与副表不受影响；
 *  - 导出的 .lss 再导入，主题名与插件属性都还在；
 *  - 目标实例没装这个主题时，引擎**静默**把题目降级成普通长文本
 *    （Question::questionThemeNameValidator，application/models/Question.php:1541）。
 *
 * @param array<string, mixed> $context
 */
function scenarioThemeUpgradeAndReimport(array $context): array
{
    $db = $context['db'];
    $key = $context['key'];
    $surveyId = $context['surveyId'];

    $responseId = latestResponseId($db, $surveyId);
    $rowsBefore = sideTableRows($db, $surveyId, $responseId, 'QTABLE');
    installQuestionTheme();
    $rowsAfterUpgrade = sideTableRows($db, $surveyId, $responseId, 'QTABLE');
    $questionAfterUpgrade = questionRow($db, $surveyId, 'QTABLE');

    $lss = exportLss($surveyId);
    $reimportedId = (int) assertRpcOk(rpc('import_survey', [$key, base64_encode($lss->asXML()), 'lss']), 'import_survey');
    $reimportedRow = questionRow($db, $reimportedId, 'QTABLE');

    uninstallQuestionTheme();
    $degradedId = (int) assertRpcOk(rpc('import_survey', [$key, base64_encode($lss->asXML()), 'lss']), 'import_survey');
    $degradedRow = questionRow($db, $degradedId, 'QTABLE');
    installQuestionTheme();

    // 先取值再删问卷：delete_survey 会连题目属性一起清掉。
    $reimportedColumns = questionAttribute($db, (int) $reimportedRow['qid'], 'mjy_table_columns');
    $degradedColumns = questionAttribute($db, (int) $degradedRow['qid'], 'mjy_table_columns');
    rpc('delete_survey', [$key, $reimportedId]);
    rpc('delete_survey', [$key, $degradedId]);

    $checks = [
        '主题重装不动已有答卷的副表' => $rowsAfterUpgrade === $rowsBefore && $rowsBefore !== [],
        '主题重装不动题目上的主题名' => $questionAfterUpgrade['question_theme_name'] === THEME_NAME,
        '导出的 .lss 带主题名' => strpos($lss->asXML(), THEME_NAME) !== false,
        '重新导入后主题名还在' => $reimportedRow['question_theme_name'] === THEME_NAME,
        '重新导入后插件属性还在' => $reimportedColumns !== null && json_decode($reimportedColumns, true) !== null,
        '重新导入后结构闸门还在' => strpos((string) $reimportedRow['preg'], 'rows') !== false,
        '主题缺失时引擎静默降级（引擎缺口）' => $degradedRow['question_theme_name'] === 'longfreetext',
        '降级后插件属性仍留在库里' => $degradedColumns !== null,
    ];
    return result('主题升级与重新导入', $checks);
}
