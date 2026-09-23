<?php

namespace ls\tests;

/**
 * P0-00.3：插件与引擎的接缝。
 *
 * 覆盖三件事：
 *  1. 题型主题元数据（newQuestionAttributes）在引擎里确实被读到；
 *  2. 题型主题没装时导入会**静默降级**（Question::questionThemeNameValidator），
 *     这是发布链路上必须由平台自己挡住的引擎缺口；
 *  3. 答卷保存/删除时结构化副表与上传会话表的投影。
 */
class MjyQuestionExtensionsTest extends TestBaseClass
{
    private const PLUGIN_NAME = 'MjyQuestionExtensions';
    private const THEME_NAME = 'mjy-repeating-table';
    private const THEME_PATH = 'themes/question/mjy-repeating-table';
    private const TABLE_CODE = 'QTABLE';
    private const UPLOAD_CODE = 'QUPLOAD';
    private const FIXTURE = '/platform/tests/fixtures/surveys/mjy-question-slice.lss';

    /** @var \MjyQuestionExtensions */
    private static $plugin;

    public static function setUpBeforeClass(): void
    {
        parent::setUpBeforeClass();
        self::installQuestionTheme();
        $pluginRecord = self::installAndActivatePlugin(self::PLUGIN_NAME);
        self::$plugin = \App()->getPluginManager()->loadPlugin(self::PLUGIN_NAME, $pluginRecord->id);
        self::assertInstanceOf(\MjyQuestionExtensions::class, self::$plugin, 'Plugin should load');
        self::$plugin->ensureSchema();

        self::importSurvey(self::fixturePath());
        (new \SurveyActivator(self::$testSurvey))->activate();
    }

    public static function tearDownAfterClass(): void
    {
        self::deActivatePlugin(self::PLUGIN_NAME);
        parent::tearDownAfterClass();
    }

    public function testPluginDeclaresRepeatingTableAttributesForLongFreeText()
    {
        $definitions = \QuestionAttribute::getOwnQuestionAttributesViaPlugin();

        $this->assertArrayHasKey('mjy_table_columns', $definitions);
        $this->assertArrayHasKey('mjy_table_min_rows', $definitions);
        $this->assertArrayHasKey('mjy_table_max_rows', $definitions);
        $this->assertStringContainsString(
            \Question::QT_T_LONG_FREE_TEXT,
            $definitions['mjy_table_columns']['types']
        );
        // 列定义是 JSON，必须绕开 XSS 过滤，否则引号与中括号会被改写。
        $this->assertFalse($definitions['mjy_table_columns']['xssfilter']);
    }

    public function testThemeMetadataSurvivesTheLssImportWhenTheThemeIsInstalled()
    {
        $question = $this->question(self::TABLE_CODE);

        $this->assertSame(self::THEME_NAME, $question->question_theme_name);
        $this->assertSame('1', (string) $this->attribute($question->qid, 'mjy_table_min_rows'));
        $this->assertNotEmpty($this->attribute($question->qid, 'mjy_table_columns'));
        $this->assertSame(
            ['item', 'qty', 'note'],
            \MjyTableColumnSpec::fromJson($this->attribute($question->qid, 'mjy_table_columns'))->codes()
        );
    }

    public function testImportSilentlyDowngradesTheQuestionWhenTheThemeIsNotInstalled()
    {
        // 引擎缺口：主题不在 lime_question_themes 里时，Question::questionThemeNameValidator()
        // 直接把 question_theme_name 换成基础主题，既不报错也不写 importwarnings。
        self::uninstallQuestionTheme();
        try {
            self::importSurvey(self::fixturePath());
            $downgraded = \Question::model()->findByAttributes([
                'sid' => self::$surveyId,
                'title' => self::TABLE_CODE,
            ]);
            $this->assertSame('longfreetext', $downgraded->question_theme_name);
        } finally {
            \Survey::model()->deleteSurvey(self::$surveyId);
            self::installQuestionTheme();
            self::importSurvey(self::fixturePath());
            (new \SurveyActivator(self::$testSurvey))->activate();
        }
    }

    public function testValidAnswerIsProjectedIntoTheSideTable()
    {
        $responseId = $this->saveResponse('{"v":1,"rows":[{"item":"录音笔","qty":"2","note":""}]}');

        self::$plugin->projectResponse(self::$surveyId, $responseId);

        $this->assertSame(
            [['item' => '录音笔', 'qty' => '2', 'note' => '']],
            $this->storedRows($responseId)
        );
        $state = $this->storedState($responseId);
        $this->assertSame(1, (int) $state['is_valid']);
        $this->assertSame(1, (int) $state['row_count']);
    }

    public function testInvalidAnswerIsRecordedAsInvalidAndLeavesNoRows()
    {
        // 引擎已经把原始文本存进答卷表了，副表必须能告诉平台「这条不可信」。
        $responseId = $this->saveResponse('{"v":1,"rows":[{"item":"","qty":"abc"}]}');

        self::$plugin->projectResponse(self::$surveyId, $responseId);

        $this->assertSame([], $this->storedRows($responseId));
        $state = $this->storedState($responseId);
        $this->assertSame(0, (int) $state['is_valid']);
        $this->assertNotEmpty($state['errors']);
    }

    public function testReprojectionReplacesEarlierRowsInsteadOfAppending()
    {
        $responseId = $this->saveResponse('{"v":1,"rows":[{"item":"甲","qty":"1"},{"item":"乙","qty":"2"}]}');
        self::$plugin->projectResponse(self::$surveyId, $responseId);

        $this->updateAnswer($responseId, '{"v":1,"rows":[{"item":"丙","qty":"3"}]}');
        self::$plugin->projectResponse(self::$surveyId, $responseId);

        $this->assertSame([['item' => '丙', 'qty' => '3', 'note' => '']], $this->storedRows($responseId));
    }

    public function testDeletingAResponsePurgesItsSideTableRows()
    {
        $responseId = $this->saveResponse('{"v":1,"rows":[{"item":"甲","qty":"1"}]}');
        self::$plugin->projectResponse(self::$surveyId, $responseId);
        $this->assertNotSame([], $this->storedRows($responseId));

        \Response::model(self::$surveyId)->findByPk($responseId)->delete();

        $this->assertSame([], $this->storedRows($responseId));
        $this->assertNull($this->storedState($responseId));
    }

    public function testUploadSessionIsOpenedBeforeTheResponseExistsAndBoundAfterwards()
    {
        // beforeProcessFileUpload 在答卷行还不存在时也会触发（responseId 为 null）。
        $token = self::$plugin->openUploadSession(self::$surveyId, null, self::UPLOAD_CODE, [
            'fieldname' => 'dummy',
            'filename' => '现场录音.mp3',
            'randfilename' => 'futmp_abcdefghijklmno_mp3',
            'ext' => 'mp3',
            'size' => 12.5,
        ]);
        $this->assertMatchesRegularExpression('/^[0-9a-f-]{36}$/', $token);

        $responseId = $this->saveResponse('{"v":1,"rows":[{"item":"甲","qty":"1"}]}');
        $bound = self::$plugin->bindUploadSessions(self::$surveyId, $responseId, self::UPLOAD_CODE, [
            ['name' => '现场录音.mp3', 'size' => 12.5, 'filename' => 'fu_zzzzzzzzzzzzzzz'],
        ]);

        $this->assertSame(1, $bound);
        $session = self::$plugin->uploadSessions()->fetchSessions(
            self::$surveyId,
            self::$plugin->currentGeneration(self::$surveyId),
            $responseId,
            self::UPLOAD_CODE
        )[0];
        $this->assertSame('bound', $session['state']);
        $this->assertSame('fu_zzzzzzzzzzzzzzz', $session['stored_name']);
        $this->assertSame($token, $session['upload_token']);
    }

    public function testOneBrokenColumnSpecDoesNotDisableTheGateForOtherQuestions()
    {
        // 失败开放是最糟的失败方式：A 题的列定义坏掉不能让 B 题的作答绕过校验。
        $broken = $this->addRepeatingTableQuestion('QBROKEN', 'not a column spec')['qid'];
        self::$plugin->resetRequestState();
        $table = $this->question(self::TABLE_CODE);
        $original = $_POST;
        try {
            $_POST = [
                'Q' . $broken => '{"v":1,"rows":[]}',
                'Q' . $table->qid => '{"v":1,"rows":[{"item":"","qty":"abc"}]}',
            ];
            $errors = self::$plugin->gatePostedAnswers((int) self::$surveyId);

            $this->assertArrayHasKey('Q' . $table->qid, $errors, '合法配置的题目仍然要被校验');
            $this->assertArrayHasKey('Q' . $broken, $errors, '坏配置必须被报出来而不是被吞掉');
            $this->assertSame('', $_POST['Q' . $table->qid], '非法作答必须被清空');
            $this->assertSame('', $_POST['Q' . $broken], '配置坏掉的题目也要拦下');
        } finally {
            $_POST = $original;
            $this->removeQuestion($broken);
            self::$plugin->resetRequestState();
        }
    }

    public function testPluginDeclaresTheJsonBearingAttributesWithoutXssFiltering()
    {
        // 净化会把 JSON 的引号与 URL 的 & 改写掉，而 xssfilter 必须是真布尔值：
        // 题型主题 config.xml 里的 <xssfilter>false</xssfilter> 只是字符串，起不到作用。
        $definitions = \QuestionAttribute::getOwnQuestionAttributesViaPlugin();

        $jsonBearing = [
            'mjy_structure_version', 'mjy_heatmap_image', 'mjy_option_groups',
            'mjy_loop_objects', 'mjy_pk_items', 'mjy_pk_pairs',
        ];
        foreach ($jsonBearing as $name) {
            $this->assertArrayHasKey($name, $definitions);
            $this->assertFalse($definitions[$name]['xssfilter'], $name . ' 存的不是 HTML');
        }
        $this->assertStringContainsString(\Question::QT_L_LIST, $definitions['mjy_option_groups']['types']);
    }

    public function testOptionGroupsAreDeclaredForBothSingleAndMultipleChoice()
    {
        // R02-04 的多选分支：分组定义走同一个属性，但引擎按 types 决定这道题
        // 认不认识它——漏掉 M 的话，导入多选题时 mjy_option_groups 会被丢掉，
        // 主题拿到空分组，页面静默退回平铺（ADR 0006 决定 6 的同类故障）。
        $definitions = \QuestionAttribute::getOwnQuestionAttributesViaPlugin();

        $types = $definitions['mjy_option_groups']['types'];
        $this->assertStringContainsString(\Question::QT_L_LIST, $types);
        $this->assertStringContainsString(\Question::QT_M_MULTIPLE_CHOICE, $types);
    }

    public function testEveryStructuredThemeIsRecognisedAsAStructuredQuestion()
    {
        // 热力图（R02-19）与自增表格（R02-13）共用同一套信封、校验器与副表，
        // 只是列定义由平台生成；插件按主题名认题，认漏一个＝这道题的作答无人校验。
        $question = $this->question(self::TABLE_CODE);
        $db = \App()->getDb();
        try {
            foreach (\MjyThemedQuestionMap::STRUCTURED_THEMES as $theme) {
                $db->createCommand()->update(
                    $db->tablePrefix . 'questions',
                    ['question_theme_name' => $theme],
                    'qid = :qid',
                    [':qid' => $question->qid]
                );
                $map = \MjyThemedQuestionMap::forSurvey($db, (int) self::$surveyId);
                $this->assertArrayHasKey(self::TABLE_CODE, $map->structuredQuestions(), $theme);
            }
        } finally {
            $db->createCommand()->update(
                $db->tablePrefix . 'questions',
                ['question_theme_name' => self::THEME_NAME],
                'qid = :qid',
                [':qid' => $question->qid]
            );
            self::$plugin->resetRequestState();
        }
    }

    public function testProjectionStampsTheStructureVersionDeclaredOnTheQuestion()
    {
        $question = $this->question(self::TABLE_CODE);
        $responseId = $this->saveResponse('{"v":1,"rows":[{"item":"甲","qty":"1"}]}');

        // 属性缺失（本契约之前发布的问卷）：标成「不知道是哪一版」，不猜。
        self::$plugin->projectResponse(self::$surveyId, $responseId);
        $this->assertSame(
            \MjyStructuredAnswerStore::LEGACY_STRUCTURE_VERSION,
            (string) $this->storedState($responseId)[\MjyStructuredAnswerStore::STRUCTURE_VERSION_COLUMN]
        );

        $this->setAttribute((int) $question->qid, 'mjy_structure_version', 'rt7');
        try {
            self::$plugin->projectResponse(self::$surveyId, $responseId);

            $this->assertSame(
                'rt7',
                (string) $this->storedState($responseId)[\MjyStructuredAnswerStore::STRUCTURE_VERSION_COLUMN]
            );
            $this->assertSame(
                ['rt7'],
                self::$plugin->structuredAnswers()->fetchStructureVersions(
                    self::$surveyId,
                    self::$plugin->currentGeneration(self::$surveyId),
                    $responseId,
                    self::TABLE_CODE
                )
            );
        } finally {
            $this->removeAttribute((int) $question->qid, 'mjy_structure_version');
        }
    }

    public function testRejectionMessageIsHtmlEscapedBeforeItReachesTheTemplate()
    {
        // man_message 会被模板以 raw 渲染（valid_message_and_help.twig:25）。
        $rendered = \MjyQuestionExtensions::renderRejection('<img src=x onerror=alert(1)>');

        $this->assertStringNotContainsString('<img', $rendered);
        $this->assertStringContainsString('&lt;img', $rendered);
    }

    // ------------------------------------------------------------- helpers

    private static function fixturePath(): string
    {
        return \Yii::app()->getConfig('rootdir') . self::FIXTURE;
    }

    private static function installQuestionTheme(): void
    {
        $theme = \QuestionTheme::model()->findByAttributes(['name' => self::THEME_NAME]);
        if ($theme === null) {
            $theme = new \QuestionTheme();
        }
        $theme->importManifest(self::THEME_PATH . '/survey/questions/answer/longfreetext', true, true);
    }

    private static function uninstallQuestionTheme(): void
    {
        $theme = \QuestionTheme::model()->findByAttributes(['name' => self::THEME_NAME]);
        if ($theme !== null) {
            $theme->delete();
        }
    }

    /**
     * 直接写库建一道题：引擎禁止给已激活问卷新增题目
     * （Question::beforeSave()，application/models/Question.php:1011），
     * 而这里要造的恰恰是「线上问卷里有一道配置坏掉的题」。
     *
     * @return array{qid: int}
     */
    private function addRepeatingTableQuestion(string $code, string $columnsJson): array
    {
        $db = \App()->getDb();
        $sibling = $this->question(self::TABLE_CODE);
        $db->createCommand()->insert($db->tablePrefix . 'questions', [
            'sid' => self::$surveyId,
            'gid' => $sibling->gid,
            'parent_qid' => 0,
            'type' => \Question::QT_T_LONG_FREE_TEXT,
            'title' => $code,
            'preg' => '',
            'other' => 'N',
            'mandatory' => 'Y',
            'encrypted' => 'N',
            'question_order' => 99,
            'scale_id' => 0,
            'same_default' => 0,
            'relevance' => '1',
            'question_theme_name' => self::THEME_NAME,
        ]);
        $qid = (int) $db->createCommand()
            ->select('MAX(qid)')
            ->from($db->tablePrefix . 'questions')
            ->where('sid = :sid AND title = :title', [':sid' => self::$surveyId, ':title' => $code])
            ->queryScalar();
        $this->assertGreaterThan(0, $qid, 'Helper question should exist');

        $db->createCommand()->insert($db->tablePrefix . 'question_attributes', [
            'qid' => $qid,
            'attribute' => 'mjy_table_columns',
            'value' => $columnsJson,
        ]);
        return ['qid' => $qid];
    }

    private function removeQuestion(int $qid): void
    {
        $db = \App()->getDb();
        $db->createCommand()->delete($db->tablePrefix . 'question_attributes', 'qid = :qid', [':qid' => $qid]);
        $db->createCommand()->delete($db->tablePrefix . 'questions', 'qid = :qid', [':qid' => $qid]);
    }

    private function question(string $code): \Question
    {
        $question = \Question::model()->findByAttributes(['sid' => self::$surveyId, 'title' => $code]);
        $this->assertNotNull($question, "Question $code should exist");
        return $question;
    }

    private function setAttribute(int $qid, string $name, string $value): void
    {
        $db = \App()->getDb();
        $this->removeAttribute($qid, $name);
        $db->createCommand()->insert($db->tablePrefix . 'question_attributes', [
            'qid' => $qid,
            'attribute' => $name,
            'value' => $value,
        ]);
    }

    private function removeAttribute(int $qid, string $name): void
    {
        $db = \App()->getDb();
        $db->createCommand()->delete(
            $db->tablePrefix . 'question_attributes',
            'qid = :qid AND attribute = :name',
            [':qid' => $qid, ':name' => $name]
        );
    }

    private function attribute(int $qid, string $name): ?string
    {
        $row = \QuestionAttribute::model()->findByAttributes(['qid' => $qid, 'attribute' => $name]);
        return $row === null ? null : $row->value;
    }

    /**
     * LimeSurvey 7 的答卷列名是 Q<qid>（application/helpers/common_helper.php:1759）。
     */
    private function fieldName(string $code): string
    {
        return 'Q' . $this->question($code)->qid;
    }

    private function saveResponse(string $tableAnswer): int
    {
        $response = \Response::create(self::$surveyId);
        $response->startlanguage = 'en';
        // fixture 打开了 datestamp，startdate 是 NOT NULL。
        $response->startdate = gmdate('Y-m-d H:i:s');
        $response->datestamp = gmdate('Y-m-d H:i:s');
        $response->setAttribute($this->fieldName(self::TABLE_CODE), $tableAnswer);
        $this->assertTrue($response->encryptSave(), 'Response should be saved');
        return (int) $response->id;
    }

    private function updateAnswer(int $responseId, string $tableAnswer): void
    {
        \Response::model(self::$surveyId)->updateByPk(
            $responseId,
            [$this->fieldName(self::TABLE_CODE) => $tableAnswer]
        );
    }

    private function storedRows(int $responseId): array
    {
        return self::$plugin->structuredAnswers()->fetchRows(
            self::$surveyId,
            self::$plugin->currentGeneration(self::$surveyId),
            $responseId,
            self::TABLE_CODE
        );
    }

    private function storedState(int $responseId): ?array
    {
        return self::$plugin->structuredAnswers()->fetchState(
            self::$surveyId,
            self::$plugin->currentGeneration(self::$surveyId),
            $responseId,
            self::TABLE_CODE
        );
    }
}
