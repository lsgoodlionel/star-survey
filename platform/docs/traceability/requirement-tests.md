# 需求↔测试登记表

这张表回答一个问题：**某条需求「已完成」，凭什么？** 每一行是一条可去仓库里核对的证据。

校验器：`platform/tools/traceability`（跑法见 [README](README.md)）。它不执行这些测试，
只核对「声称的那个测试是否还在」——文件在不在、类与方法叫不叫这个名字、脚本有没有可执行位。
「测试是否通过」由各自的套件回答。

## 怎么填

| 列 | 说明 |
|---|---|
| 需求ID | 必须是 [requirement-index.md](requirement-index.md) 里的编号（即 02 矩阵里的编号） |
| 层级 | `单测`／`集成`／`端到端`。区分只为看清这条需求有没有真正跑过引擎 |
| 证据定位符 | 见下表 |
| 说明 | 这条证据钉住的是需求里的哪一句 |

定位符按后缀分三种形状，**校验器只认这三种**：

| 后缀 | 形状 | 为什么 |
|---|---|---|
| `.py` | `路径::类::方法`（也可只写顶层函数） | 走 AST，层级是真查的：方法挪到别的类就算没找到 |
| `.java` `.php` | `路径::方法`，**只能一级** | 正则只能给出「文件里出现过哪些名字」的平表，核对不了「方法真在这个类里」。接受两级就等于暗示层级查过了，所以直接拒绝 |
| `.sh` | `路径`，不接符号 | 脚本是整体入口；另查可执行位 |

同一条需求可以有多行。**登记不等于验收通过**：本表证明「有测试且测试还在」，
矩阵里那条需求的全部子项是否满足，见 `08-需求进度与方案修订（仓库实证）.md`。

## 覆盖现状

本表是逐步补齐的，**不是**305 条需求的完整映射。当前只登记了确有测试证据、
且证据已逐条核对过的那些。按工作包的覆盖统计：

```
cd platform/tools/traceability && python3 -m reqtrace.cli report
```

一条**反向闸门**已经生效：`pubgw/questions/theme_specs.py` 里每个题型主题都必填
`requirement=`，凡是声明了需求归属的主题，本表里必须有它的测试证据——新加主题而不补证据，
校验直接红。

截至 2026-10-09，校验器自身 **72 tests** 通过；索引含 **305** 条需求，机器登记 **84** 条证据、
覆盖 **40** 条需求，自有源码引用 **114** 个需求编号，并已与 02 矩阵对账通过。

产品化的人读基线现统一为：

- [`platform/docs/productization/frontend-backend-requirements.md`](../productization/frontend-backend-requirements.md)
- [`platform/docs/productization/product-blueprint.md`](../productization/product-blueprint.md)
- [`platform/docs/productization/release-roadmap.md`](../productization/release-roadmap.md)
- [`platform/docs/productization/capability-map.md`](../productization/capability-map.md)

需求矩阵审计起点保持 13 条完整达成、86 条部分完成、191 条未开始、15 条等待外部输入。

能力摘要（机器事实）：accepted=0，partial=11，not_started=1，external=0。

前三份 canonical 文档只定义需求、IA 和发布门禁；它们不是测试通过证明，也不能代替 `capabilities.json` 中需求、后端、
前端、流程、生产、追溯六层强类型证据。

### 管理端首期的局部断言索引

Task 7 将证据分为三层：**代码存在**由下列定位符与组件测试证明；**自动化通过**由 Fix Round 1 frontend
20 files / 174 tests、typecheck、lint、build、Python gate 40 tests、运行中 demo Chromium gate 和 fresh Playwright 11/11，
以及原 Task 7 backend 1421 tests 证明；**用户可见**由
`docs/audits/2026-10-09-product-alignment/07-admin-workspace-*.png` 四档人工检查证明固定名称可见且无明显遮挡、截断或入口混淆。
截图不证明 `scrollWidth`、44px 或焦点几何；这些由 Playwright DOM 行为测量。运行中 demo 另由 Chromium 自动遍历固定三层并拒绝
E2E 时间戳资源。这些证据只收口 Phase A+B，不将 `820–1179px` 的 Phase C 双栏 + 检查器、其他 Phase C/D 或 production SSL 部署标为完成。

当前定位符校验器只解析 `.py`、`.java`、`.php` 和 `.sh`，尚不能把 Vitest 的 `.ts/.tsx` 符号加入
上面的 80 条机器登记。下列补充索引给出可直接核对的测试名，并明确只证明复合需求中的局部断言：

- `R01-01`：`WorkspacePage.test.tsx` 的 `createsAProjectFolderAndBlankSurveyWithChineseDefaults`，以及
  `e2e/authoring.spec.ts` 桌面场景，证明从管理端创建空白中文问卷并进入编辑器；不证明所有应用类型。
- `R01-02`：`ImportPage.test.tsx` 的 `previewsWithoutWritingAndSelectsValidQuestionsByDefault`、
  `keepsBadLinesVisibleWithTheirOriginalLineNumbers`、`importsOnlyTheCheckedOrdinalsIntoTheChosenGroupAndRefreshesTheDraft`
  和桌面 E2E，证明“先预览、坏行定位、选择后导入”子链路。
- `R01-04`：`definition.test.ts` 的 `editsOnlyTheSelectedBasicQuestion`、
  `keepsQuestionAndGroupUuidsStableWhileReordering`，证明基础字段编辑与稳定排序；不证明拖拽排序或高级题型编辑。
- `R01-07`：`PreviewPage.test.tsx` 覆盖防重复创建、轮询失败恢复、到期收口、同 request ID
  重试、终态新建和旧 session 审计。`e2e/authoring.spec.ts` 等待真实预览事件投递后，直接比较
  `survey_published_version`、`survey_question_binding`、official route、`engine_outbox` 和
  `response_projection` 五项前后均为 0，再完成审批发布。桌面与 Pixel 7 截图位于
  `platform/docs/productization/evidence/task4/`。
- `R05-01`／`R05-07`／`R05-08`：发布后管理端真实创建投放链接；浏览器等待二维码像素完成加载，
  用 jsQR 解码并与 create response 的作答 URL 逐字比对，再经短链进入 LimeSurvey 正式作答。
- `R06-01`／`R06-02`：正式作答后通过有界 UI 手动刷新 eventual 观测摘要为 1 和明细值可见，
  不再等待固定缓存时间；随后解包真实 CSV 制品，校验 UTF-8 BOM、CRLF、字段字典映射、
  一条正式答卷及其 `A1` 值。其他导出格式不因该旅程提升为完整验收。
- `R01-08`：`EditorPage.test.tsx` 的 `savesWithTheCurrentDraftVersionAndAdoptsTheReturnedVersion`、
  `keepsLocalChangesWhenTheServerReturns409`、`recoversTheInMemoryDraftAfterReauthentication`，证明版本保存、
  冲突不覆盖与同页内存恢复；不证明多人实时协同或字段级合并。
- `R01-09`：`PublishPage.test.tsx` 的 `showsOnlyActionsAllowedByTheCurrentApprovalState` 和桌面 E2E，证明提交、
  批准和按获批版本发布的浏览器链路；其他审批状态由组件测试覆盖，不据此宣称整条需求 Accepted。
- `R19-04`：`WorkspacePage.test.tsx` 的 `usesServerCapabilitiesInsteadOfTokenRolesForAvailableActions`、平台
  `ResourceTreeApiTest.capabilitiesAreReadOnlyAuthoritativeAndPreserveNotFoundPrivacy` 和桌面 E2E，证明文件夹创建、
  资源能力与不可见性子项；不证明协作员生命周期全部完成。
- `R23-03`：`OrgLoginHandoffTest` 的无 JWT 重定向、单次交换、过期／换浏览器／跨租户拒绝用例，以及
  `AuthProvider.test.tsx` 的内存会话用例，证明管理端可信身份交接子项；不替代整套服务权限验收。
- `R23-08`：`test_admin_web_gate.py` 的 `test_issue_browser_token_never_prints_the_jwt`、
  `test_export_copies_only_allowlisted_sanitized_failure_evidence`、
  `test_export_rejects_unknown_trace_fields_and_leaves_no_stale_output`、
  `test_export_rejects_encoded_or_normalized_credentials_and_clears_output`、
  `test_export_accepts_only_real_admin_web_routes`、
  `test_export_preserves_a_bounded_sanitized_network_sequence`、
  `test_export_rejects_unsafe_network_sequences_and_clears_old_output`、
  `test_playwright_failure_exports_only_sanitized_ci_evidence` 和
  `redaction.spec.ts` 的真实 Chromium DOM/网络/截图用例，证明真实浏览器门禁不打印 JWT，只导出白名单脱敏
  失败证据，拒绝未知字段、编码或规范化后的凭据、非管理端路由与不安全网络事件，并在截图前收集脱敏响应和
  隐藏敏感控件；不代表完整安全供应链与审计要求。
- `R23-12`：`platform/deploy/test/run-admin-web-e2e.sh` 串起浏览器、平台、平台库、发布网关和引擎库，并由
  `AdminWebGateTest.test_verify_evidence_rejects_a_platform_or_engine_mismatch` 反向证明任一段不一致会失败；这是
  管理端首期纵向验收证据。Task 7 还覆盖稳定资源名、请求参数、同一问卷往返、归档恢复、
  768/819/820/1024/1440/Pixel 7 DOM 无溢出、44px 和键盘流程，并在 Playwright 成功 marker、平台 API/DB、网关与引擎 DB
  全部核对成功后才归档根项目；运行中 demo 的浏览器检查遍历固定三层并拒绝 E2E 时间戳资源。不把 305 条需求整体视为完成，
  也不据此声称 `820–1179px` 的 Phase C 双栏 + 检查器已交付。

## WP-01 创建与编辑

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R01-02 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/survey/SurveyTextImportTest.java::previewSplitsTheTextIntoQuestionsWithTheirRecognisedTypeOptionsAndLineNumbers` | 多题多选项可预览，题号与顺序一致 |
| R01-02 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/survey/SurveyTextImportTest.java::aBadLineIsReportedWithItsLineNumberAndDoesNotStopTheRestOfTheBatch` | 异常格式可定位到行，不中断整批 |
| R01-02 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/survey/SurveyTextImportTest.java::questionsWithBlockingProblemsArePreviewedButMarkedNotImportable` | 先预览后落库 |
| R01-07 | 端到端 | `platform/deploy/test/run-admin-web-e2e.sh` | 预览事件投递后正式 version/binding/route/outbox/response 五项前后不变，再显式结束与发布 |
| R01-09 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/survey/PublishApprovalFlowTest.java::anApprovedRequestLetsTheApplicantPublishExactlyTheApprovedVersion` | 批准绑定提交时的草稿版本 |
| R01-09 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/survey/PublishApprovalFlowTest.java::editingTheDraftAfterApprovalVoidsTheApprovalAndPublishIsRefused` | 批准后改稿即失效 |
| R01-09 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/survey/PublishApprovalApiTest.java::aSubAccountMustGoThroughApprovalToPublishOverTheApi` | 子账户经 API 也绕不过审批 |

## WP-02 题型与采集组件

下面 15 条与 `theme_specs.py` 的 `requirement=` 一一对应（反向闸门就查这一组）。

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R02-03 | 单测 | `platform/tools/publish-gateway/tests/test_question_cascading.py::CascadingColumnsTest::test_every_level_is_a_dictionary_column_pinned_to_one_version` | 多级下拉：每级一列，钉死字典某一版 |
| R02-03 | 单测 | `platform/tools/publish-gateway/tests/test_question_cascading.py::DictionarySnapshotTest::test_the_shipped_digest_must_match_the_one_the_question_carries` | 字典快照与题目引用对账 |
| R02-03 | 端到端 | `platform/deploy/test/run-question-themes.sh` | 引擎侧服务端判整条路径 |
| R02-04 | 单测 | `platform/tools/publish-gateway/tests/test_question_themes.py::GroupedOptionsTest::test_an_option_left_out_of_every_group_is_rejected` | 分组必须恰好覆盖全部选项 |
| R02-04 | 单测 | `platform/tools/publish-gateway/tests/test_question_themes.py::GroupedOptionsMultipleChoiceTest::test_a_subquestion_left_out_of_every_group_is_rejected` | 多选分支按子题代码分组 |
| R02-07 | 单测 | `platform/tools/publish-gateway/tests/test_question_themes.py::InlineBlankTest::test_every_blank_gets_a_server_side_length_rule` | 选项内嵌填空：每处填空都有服务端长度闸门 |
| R02-11 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::LoopRatingTest::test_the_row_count_is_pinned_to_the_number_of_objects` | 循环评价：行数钉死成评价对象个数 |
| R02-11 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::LoopRatingTest::test_one_enum_column_per_dimension_over_the_declared_scale` | 每个维度一列，取值限定在声明的量表内 |
| R02-13 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::RepeatingTableTest::test_columns_and_bounds_compile_to_the_plugin_attributes` | 自增表格：列定义与行数上下限随 .lss 发布 |
| R02-13 | 端到端 | `platform/deploy/test/run-question-themes.sh` | 引擎侧按列定义逐格校验并投影副表 |
| R02-14 | 单测 | `platform/tools/publish-gateway/tests/test_question_themes.py::MatrixStepperTest::test_column_shape_is_unchanged` | 矩阵单题作答：答卷仍是每行子题一列 |
| R02-17 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::ImagePkTest::test_each_pair_becomes_a_choice_column_over_its_own_two_items` | 图片 PK：配对由平台声明，一对一列 |
| R02-17 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::ImagePkTest::test_each_pair_also_records_which_image_was_shown_first` | 展示位置随作答一并留痕 |
| R02-18 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::ShelfTest::test_the_product_column_is_a_unique_enum_over_the_declared_products` | 货架题：取了什么 |
| R02-18 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::ShelfTest::test_the_quantity_column_is_a_bounded_integer` | 货架题：取了几件 |
| R02-19 | 单测 | `platform/tools/publish-gateway/tests/test_question_side_tables.py::HeatmapTest::test_generated_columns_pin_the_coordinate_range` | 热力图选区：归一化坐标范围由平台写死 |
| R02-19 | 端到端 | `platform/deploy/test/run-question-themes.sh` | 引擎侧逐格校验热区坐标 |
| R02-20 | 单测 | `platform/tools/publish-gateway/tests/test_question_media_types.py::CarouselSlidesTest::test_every_slide_needs_alternative_text` | 轮播图：替代文本是验收项，不是可选装饰 |
| R02-20 | 单测 | `platform/tools/publish-gateway/tests/test_question_media_types.py::CarouselSlidesTest::test_the_pinned_asset_version_is_required` | 图来自平台资产服务，版本被钉死 |
| R02-22 | 单测 | `platform/tools/publish-gateway/tests/test_question_research_types.py::TextHighlightTest::test_a_segment_code_carries_its_offset_and_a_fingerprint_of_its_own_text` | 文字点睛：中文标记偏移与原文版本一致 |
| R02-22 | 单测 | `platform/tools/publish-gateway/tests/test_question_research_types.py::TextHighlightTest::test_editing_the_source_text_changes_the_structure_digest` | 改原文即换结构版本 |
| R02-28 | 单测 | `platform/tools/publish-gateway/tests/test_question_themes.py::ScanInputTest::test_a_scan_question_must_bound_what_the_client_may_submit` | 扫码录入：客户端提交的整串必须有长度闸门 |
| R02-43 | 单测 | `platform/tools/publish-gateway/tests/test_question_themes.py::CollapsibleTest::test_column_shape_is_unchanged` | 折叠栏目不收集作答 |
| R02-46 | 单测 | `platform/tools/publish-gateway/tests/test_question_research_types.py::PsychTrialTest::test_the_reaction_time_is_a_bounded_integer_in_milliseconds` | 心理实验：记按键与毫秒反应时 |
| R02-46 | 单测 | `platform/tools/publish-gateway/tests/test_question_research_types.py::PsychTrialTest::test_there_is_no_column_the_respondent_could_claim_correctness_in` | 正确率由平台算，作答者写不了 |
| R02-47 | 单测 | `platform/tools/publish-gateway/tests/test_question_research_types.py::KanoModelTest::test_both_questions_share_the_scale_the_model_fixes` | 专业模型：量表由模型固定 |
| R02-47 | 单测 | `platform/tools/publish-gateway/tests/test_question_research_types.py::KanoModelTest::test_the_scale_is_not_an_author_supplied_option` | 作者改不了模型量表 |

## WP-04 访问与作答规则

R04-02／03／05 是**部分**覆盖，口径见 `platform/docs/p2/progress.md`：邀请码只做了
参与者 token＋`access_mode=C`；地区规则是可插拔数据源，无数据按 `regionUnknown` 处理。

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R04-01 | 单测 | `platform/tools/publish-gateway/tests/test_policy_schema.py::AccessTest::test_plaintext_password_never_reaches_the_engine` | 统一访问密码：明文不落库 |
| R04-01 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_password` | 真实引擎上的口令闸门 |
| R04-02 | 单测 | `platform/tools/publish-gateway/tests/test_policy_schema.py::AccessTest::test_invitation_requires_participants` | 邀请码必须有参与者名单 |
| R04-02 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_ref_only_participants` | 只认名单内的 token |
| R04-03 | 单测 | `platform/tools/publish-gateway/tests/test_policy_schema.py::LimitsTest::test_identity_must_be_known` | 限次身份只能是已知的那几种 |
| R04-03 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_device_limit` | 按设备限次 |
| R04-03 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_token_limit` | 按 token 限次 |
| R04-04 | 单测 | `platform/tools/publish-gateway/tests/test_policy_compile.py::WindowConversionTest::test_shanghai_local_time_becomes_utc` | 时区窗口按服务端时钟换算 |
| R04-04 | 单测 | `platform/tools/publish-gateway/tests/test_policy_compile.py::WindowConversionTest::test_window_crossing_a_dst_change_keeps_real_duration` | 跨夏令时窗口时长不变 |
| R04-04 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_not_open` | 未开放时拒绝 |
| R04-04 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_closed` | 已关闭时拒绝（含改过的客户端时钟） |
| R04-05 | 单测 | `platform/tools/publish-gateway/tests/test_policy_schema.py::NetworkTest::test_invalid_cidr_is_rejected` | IP 规则：非法 CIDR 不放行 |
| R04-05 | 单测 | `platform/tools/publish-gateway/tests/test_policy_schema.py::NetworkTest::test_region_unknown_is_deny_or_allow` | 地区无数据时的口径必须显式声明 |
| R04-05 | 端到端 | `platform/tests/e2e/access_policy.py::scenario_network` | 真实引擎上的 IP 闸门 |
| R04-05 | 端到端 | `platform/deploy/test/run-access-policy.sh` | 整套访问策略的端到端入口 |

## WP-05 发布触达与激励

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R05-01 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryLinkTest.java::theLinkPointsAtTheEnginesurveyAndCarriesTheSignedParameters` | 投放链接带签名参数 |
| R05-01 | 端到端 | `platform/deploy/test/run-admin-web-e2e.sh` | 二维码像素可解码且 payload 等于刚创建的作答 URL，再经链接完成正式作答 |
| R05-04 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliverySendTest.java::aCrashMidTaskResumesWithoutSendingAnythingTwice` | 批量投放：恢复不重发 |
| R05-04 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryReceiptTest.java::aDeliveredReceiptIsCountedOnceNoMatterHowOftenItIsRedelivered` | 回执重复投递只计一次 |
| R05-05 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryReceiptTest.java::aReceiptWithoutAValidSignatureIsRejectedAndLeavesNoTrace` | 渠道商回执验签 |
| R05-05 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryReceiptTest.java::anOldTimestampIsRejectedEvenWithAValidSignature` | 回执防重放 |
| R05-06 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryReminderTest.java::someoneWhoAlreadyCompletedIsNotReminded` | 已完成的人不再被催 |
| R05-06 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryReminderTest.java::theReminderCapIsRespected` | 催答次数有上限 |
| R05-07 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryLinkTest.java::theShortLinkResolvesToTheSameRespondentUrl` | 短链解析到同一作答地址 |
| R05-07 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryLinkTest.java::unknownRevokedAndExpiredShortLinksAllLookIdentical` | 失效短链不泄露状态差异 |
| R05-08 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/shared/security/SignedParametersTest.java::rejectsATamperedValue` | 签名参数不可篡改 |
| R05-08 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/shared/security/SignedParametersTest.java::rejectsAnAddedParameter` | 不可增参 |
| R05-08 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/shared/security/SignedParametersTest.java::rejectsASignatureMadeForAnotherLink` | 签名不可跨链接复用 |
| R05-09 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryNotificationTest.java::severalNewResponsesAreMergedIntoOneMessageWithMinimalFields` | 新答卷通知合并且字段最小化 |
| R05-09 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/delivery/DeliveryNotificationTest.java::aMemberWhoLostAccessStopsReceivingNotifications` | 失权后不再收到通知 |

## WP-06 数据管理与输出

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R06-01 | 端到端 | `platform/deploy/test/run-admin-web-e2e.sh` | 正式作答经引擎事件投影后，通过有界 UI 刷新在摘要和明细页可见 |
| R06-02 | 端到端 | `platform/deploy/test/run-admin-web-e2e.sh` | 下载的 ZIP 含 UTF-8 BOM/CRLF CSV，字段字典映射到一条真实答卷值 `A1` |
| R06-03 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/response/SavExportFormatTest.java::valueLabelsAreAttachedToBothNumericAndShortStringVariables` | SPSS 值标签 |
| R06-03 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/response/SavExportFormatTest.java::cellsRoundTripWithSystemMissingForBlanksAndUtf8SafeTruncation` | 缺失值与 UTF-8 截断 |
| R06-03 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/response/ResponseExportSavTest.java::theBundleHasTheDatasetItsVariableDictionaryAndTheUsualSideSheets` | 整条作业链路产出 SPSS 包与变量字典 |
| R06-05 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/response/DocxExportFormatTest.java::eachResponseGetsItsOwnHeadingAndPage` | 逐份答卷成文 |
| R06-05 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/response/ResponseExportDocxTest.java::everyResponseBecomesItsOwnSectionWithQuestionsAndAnswers` | 整条作业链路产出 Word |
| R06-06 | 单测 | `platform/services/business/src/test/java/cn/mjy/platform/response/DocxExportFormatTest.java::sideTableAnswersArePivotedBackIntoARealTable` | 副表作答还原成表格 |
| R06-07 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/response/ResponseExportAttachmentPackageTest.java::thePackageCarriesTheFilesThemselvesAlongsideTheManifest` | 附件包含文件本体与清单 |
| R06-07 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/response/ResponseExportAttachmentPackageTest.java::aTransientFailureRetriesAndOnlyRefetchesWhatIsStillMissing` | 失败项可重试 |
| R06-07 | 单测 | `plugins/MjyQuestionExtensions/tests/MjyAttachmentFileEndpointTest.php::testTheEndpointNeverHoldsTheFileContents` | 引擎侧取件端点不把文件内容留在内存里（内存有界） |

## WP-18 通讯录联系人用户

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R18-01 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/contacts/ContactImportDedupeTest.java::importDedupesAgainstExistingContactsAndWithinTheFileByTheConfiguredKey` | 全量增量同步的去重键 |
| R18-01 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/contacts/ContactImportDedupeTest.java::invalidRowsAreReportedWithoutAbortingTheImport` | 异常行可定位且不中断导入 |
| R18-01 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/identity/org/OrgDepartureTest.java::departureRemovesTheMemberAndFreesTheSeat` | 离职后立即失权 |
| R18-02 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/contacts/ContactTagTest.java::anotherTenantsTagCannotBeAssigned` | 标签权限隔离 |
| R18-02 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/contacts/ContactOrgUnitScopeTest.java::aDepartmentAdminSeesOnlyItsOwnDepartmentAndDescendants` | 部门级数据范围 |

## WP-19 团队品牌

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R19-05 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/survey/template/SurveyTemplateLifecycleTest.java::aTemplateCarriesNoParticipantsNoAccessPasswordAndNoAdminContacts` | 企业模板断开原项目数据 |
| R19-05 | 集成 | `platform/services/business/src/test/java/cn/mjy/platform/survey/template/SurveyTemplateLifecycleTest.java::aNewTemplateIsNotVisibleToTheTenantUntilItIsApproved` | 未批准的模板越权不可见 |

## WP-23 共同基础与交付保障

| 需求ID | 层级 | 证据定位符 | 说明 |
|---|---|---|---|
| R23-12 | 单测 | `platform/tools/traceability/tests/test_check.py::RepositoryTest::test_the_repository_passes_every_gate` | 需求↔测试映射本身可机器校验 |
| R23-12 | 单测 | `platform/tools/traceability/tests/test_locators.py::PythonLocatorTest::test_a_renamed_method_is_not_found` | 测试改名后声称的覆盖会红 |
