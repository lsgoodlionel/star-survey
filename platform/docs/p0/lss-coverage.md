# P0-00.6 LSS 字段覆盖面

引擎版本 LimeSurvey 7.1.2。导出入口 `surveyGetXMLData()`（`application/helpers/export_helper.php:1051`），
其中结构部分由 `surveyGetXMLStructure()`（同文件 `:874`）写出；导入入口 `XMLImportSurvey()`
（`application/helpers/admin/import_helper.php:2168`）。

实测脚本 `platform/tests/e2e/publish_roundtrip.php`（场景「LSS 覆盖面」），
运行方式见 [ADR 0005](../adr/0005-publishing.md)。

## 一、LSS 携带的小节

`<document>` 下的顶层小节。「导出」「导入」两列给出实际写出/读入该小节的代码行。

| LSS 小节 | DB 表 | 导出 | 导入 | 备注 |
|---|---|---|---|---|
| `LimeSurveyDocType` / `DBVersion` | — | `export_helper.php:1058-1059` | `import_helper.php:2180,2191` | 文档类型必须是 `Survey`，否则整体拒绝 |
| `languages` | `lime_surveys.language` ＋ `additional_languages` | `export_helper.php:1060-1066` | `import_helper.php:2219` | 决定后续所有 l10n 行的语言集合 |
| `answers` | `lime_answers` | `export_helper.php:883` | `import_helper.php:2951` | 答案选项本体（`code`、`sortorder`、`assessment_value`、`scale_id`） |
| `answer_l10ns` | `lime_answer_l10ns` | `export_helper.php:892` | `import_helper.php:2958` | 选项文案，按语言一行 |
| `assessments` | `lime_assessments` | `export_helper.php:899` | `import_helper.php:3265` | 评分规则 |
| `conditions` | `lime_conditions` | `export_helper.php:907` | `import_helper.php:3160` | 导入后立刻被 `UpgradeConditionsToRelevance()` 转成相关性表达式（`import_helper.php:3560-3561`） |
| `defaultvalues` | `lime_defaultvalues` | `export_helper.php:913` | `importDefaultValues()`，`import_helper.php:3155` | |
| `defaultvalue_l10ns` | `lime_defaultvalue_l10ns` | `export_helper.php:918` | 同上 | |
| `groups` | `lime_groups` | `export_helper.php:926` | `import_helper.php:2489` | gid 全部重编号 |
| `group_l10ns` | `lime_group_l10ns` | `export_helper.php:934` | `import_helper.php:2500` | |
| `questions` | `lime_questions`（`parent_qid=0`） | `export_helper.php:941` | `import_helper.php:2594` | 含 `question_theme_name`，但不含主题定义本身 |
| `subquestions` | `lime_questions`（`parent_qid>0`） | `export_helper.php:948` | `import_helper.php:2744` | 与父题同表，导出时按 `parent_qid` 拆成两个小节 |
| `question_l10ns` | `lime_question_l10ns` | `export_helper.php:956` | `import_helper.php:2602` | 题干与帮助文本 |
| `question_attributes` | `lime_question_attributes` | `export_helper.php:972` | `import_helper.php:3053` | 题目高级设置；导入后 `checkWrongQuestionAttributes()` 会纠正 Y/N 写法（`import_helper.php:3571`） |
| `quota` | `lime_quota` | `export_helper.php:979` | `import_helper.php:3316` | |
| `quota_members` | `lime_quota_members` | `export_helper.php:985` | `import_helper.php:3341` | |
| `quota_languagesettings` | `lime_quota_languagesettings` | `export_helper.php:992` | `import_helper.php:3382` | |
| `surveys` | `lime_surveys` | `export_helper.php:1007` | `import_helper.php:2226` | 排除 `owner_id`、`active`、`datecreated`（`export_helper.php:1002`） |
| `surveys_languagesettings` | `lime_surveys_languagesettings` | `export_helper.php:1025` | `import_helper.php:2337` | 邮件模板附件被转成 JSON 后导出（`export_helper.php:1019-1021`） |
| `survey_url_parameters` | `lime_survey_url_parameters` | `export_helper.php:1031` | `import_helper.php:3425` | |
| `plugin_settings` | `lime_plugin_settings`（`model='Survey'`） | `export_helper.php:1037` | `import_helper.php:3448` | 目标实例没装同名插件时整段跳过并告警（`import_helper.php:3467`） |
| `surveys_groups` | `lime_surveys_groups` | `export_helper.php:1045` | `import_helper.php:2293` | **只读作用**：仅按 `name` 查找目标实例已有的问卷组，找不到就落到 `gsid=1`；不会创建问卷组 |
| `themes` / `themes_inherited` | `lime_template_configuration` | `surveyGetThemeConfiguration()`，`export_helper.php:3368`，由 `:1069`、`:1071` 调用 | `import_helper.php:3488` / `:3478` | 只有主题**配置值**，没有主题文件；目标实例没有该主题时整条跳过（`import_helper.php:3499`） |

### 实测行数对照

导入 `tests/data/surveys/survey-dual-scale-question-api-test.lss` 后再导出，XML 行数与库内行数逐项一致
（MariaDB 10.11 与 PostgreSQL 16 结果相同）：

| 小节 | XML 行数 | DB 行数 |
|---|---|---|
| `groups` | 1 | 1 |
| `group_l10ns` | 2 | 2 |
| `questions` | 7 | 7 |
| `subquestions` | 10 | 10 |
| `question_l10ns` | 34 | 34 |
| `question_attributes` | 194 | 194 |
| `answers` | 19 | 19 |
| `surveys_languagesettings` | 2 | 2 |

实测导出的小节列表：
`languages, answers, answer_l10ns, groups, group_l10ns, questions, subquestions, question_l10ns,
question_attributes, surveys, surveys_languagesettings, surveys_groups, themes, themes_inherited`
（该 fixture 没有配额、条件、默认值、评分与插件设置，故对应小节为空不写出）。

## 二、LSS 不携带的内容

以下每条都在代码中核对过，**不是**推测。脚本里对应的断言在导出前先把这些数据真的造出来
（激活问卷、建参与者表、写入参与者、产生问卷权限行），再确认导出中仍然缺席。

| 内容 | DB 表 | 为什么不在 LSS 里 | 平台必须怎么办 |
|---|---|---|---|
| 参与者 / 令牌 | `lime_tokens_<sid>` | `surveyGetXMLStructure()` 里没有任何针对令牌表的查询；令牌走独立文档 `.lst`，只在 `.lsa` 归档里随 `XMLImportTokens()` 导入（`import_helper.php:1471`、`:3614`） | 名单由平台侧主数据下发，发布后用 RemoteControl `activate_tokens` ＋ `add_participants` 写入 |
| 答卷与计时 | `lime_responses_<sid>`、`lime_survey_<sid>_timings` | 同上，走 `.lsr` / `.lsi`，由 `XMLImportResponses()`（`:3687`）、`XMLImportTimings()`（`:4167`）导入 | 发布链路不搬运答卷；答卷只通过 `MjyPlatformBridge` 事件流出（[ADR 0003](../adr/0003-runtime-events.md)） |
| 问卷权限 | `lime_permissions` | 导出没有该表；导入结束时无条件执行 `Permission::model()->giveAllSurveyPermissions(Yii::app()->session['loginID'], $iNewSID)`（`import_helper.php:3550`），即**导入者独占全部权限** | 权限模型完全由平台侧承担；引擎侧只保留发布机器人账号 |
| 问卷属主与激活态 | `lime_surveys.owner_id` / `active` / `datecreated` | 导出显式排除（`export_helper.php:1002`）；导入强制 `active='N'`（`:2256`）、`owner_id=当前登录用户`（`:2258`） | 发布后必须单独调用 `activate_survey`；不能指望导入就是上线 |
| 全局题型主题 | `lime_question_themes` | 两个 helper 中都没有出现该表；`questions.question_theme_name` 只是一个名字字符串 | 题型主题作为引擎镜像的一部分预装；平台在生成 LSS 前校验主题名在目标实例存在 |
| 问卷主题文件 | 文件系统 `themes/survey/*` | 只导出 `lime_template_configuration` 的配置值（`export_helper.php:3368`）；导入时 `Template::checkIfTemplateExists()` 为假就跳过并置 `template_deleted`（`import_helper.php:3499-3502`） | 主题随镜像发布；平台校验 `template` 字段指向的主题已安装 |
| 标签集 | `lime_labelsets`、`lime_labels`、`lime_label_l10ns` | 导出没有该表；标签集走独立文档 `.lsl`，由 `XMLImportLabelsets()`（`import_helper.php:1213`）导入。`lime_answers` 没有 `lid` 外键，答案选项与标签集在运行期**没有关联** | 标签集只是作者端复用工具；平台的选项字典自行展开成 `answers` 行即可 |
| 问卷组设置 | `lime_surveys_groupsettings` | 导出只写 `surveys_groups`（`export_helper.php:1045`），不写组设置表 | 值为 `I`（继承）的问卷设置会在目标实例解析成**目标侧**的组默认值。平台生成 LSS 时必须把所有关键设置写成显式值，不能留 `I` |
| 问卷组本体 | `lime_surveys_groups` | 小节存在但导入端只用它按 `name` **查找**已有组（`import_helper.php:2293-2308`），找不到就落到 `gsid=1` | 问卷组在目标实例预建，或接受默认组 |
| 全局设置、用户、主题库 | `lime_settings_global`、`lime_users`、`lime_templates` | 都不在导出查询里 | 属于实例级配置，由部署管道管理 |
| 保存中断、问卷链接 | `lime_saved_control`、`lime_survey_links` | 不在导出查询里 | 运行期数据，不参与发布 |

## 三、导入过程中会被改写的字段

| 字段 | 行为 | 代码 |
|---|---|---|
| `sid` / `gid` / `qid` / `aid` / `dvid` / 配额 id | 全部重新分配，旧值只留在替换映射里 | `import_helper.php:2193-2196` 等 |
| 题目代码 `title` | 非法或与同问卷已有代码冲突时自动改名，并写入 `importwarnings` | `import_helper.php:2711`（父题）、`:2878`（子题） |
| `active` | 强制 `N` | `import_helper.php:2256` |
| `owner_id` | 强制为导入者 | `import_helper.php:2258` |
| `gsid` | 非复制场景强制 `1`，再按组名尝试改回 | `import_helper.php:2291-2308` |
| 条件 | 转换为相关性表达式 | `import_helper.php:3560-3561` |
| 结构完整性 | `SurveyIntegrity::fixSurveyIntegrity()` 在 LSS 导入后兜底修复 | `import_helper.php:1400-1402` |

**题目代码自动改名是发布链路上最危险的一条。** 平台以代码为映射键，一旦引擎改名，平台的
题目 UUID ↔ 代码映射就与引擎不一致。必须在发布后用 `get_fieldmap` 校验代码集合与提交的
DSL 完全一致，而不是信任导入返回值。
