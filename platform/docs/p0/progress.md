# P0 技术验证进度

- 批准：用户于 2026-09-18 批准 P0；决定不使用代理，所有内容放在当前仓库（ADR 0004）。
- 仓库：`lsgoodlionel/star-survey`，分支 `main`（根提交为 LimeSurvey 7.1.2 快照，上游 `4c20c680`；原 fork 保留为 remote `limesurvey-fork`）。
- 周期：6 周（W1 = 2026-09-18 起）。

| 编号 | 任务 | 状态 | 证据 / 下一步 |
|---|---|---|---|
| 00.1 | 需求冻结 | **决策已确认，待正式批准** | U-01～U-11、P-01～P-10 全部关闭，各工作包待确认点已依据实测结论推导关闭；分母基线＝原文 SHA-256 `1353e245…`＋矩阵 02＋冻结表 v1.0 |
| 00.2 | 引擎认证与测试基线 | **已完成** | unit 套件 MariaDB 848 例、PostgreSQL 16 840 例均 0 错误 0 失败；**13 个浏览器功能套件 189 例全绿**（约 18 分钟），初始 6 个红全部定位为本地与 CI 的环境差异，并用「插件未注册」与「插件全启用重跑结果逐字节相同」两重证据归因（[功能套件](functional-suite.md)）；**上游差异已比对**：根提交与上游 `4c20c680` 逐字节相同，HEAD 对引擎源码零修改，仅 `.gitignore` 两行＋132 个新增文件（[上游差异](upstream-diff.md)）；依赖与许可清单见 [sbom/](sbom/) |
| 00.3 | 题型纵切 | **已完成** | 三条纵切（原生数组题 `F`、自增表格＝长文本＋题型主题＋结构化副表、上传题 `|`＋上传会话）走完「起草→导入→激活→真实 HTTP 作答→断点续答→改已提交答卷→导出→重新导入/主题升级」全链路，MariaDB 与 PostgreSQL 各 7 个场景全过，插件测试 35/35 通过（[ADR 0006](../adr/0006-question-types.md)、[题型纵切](question-slice.md)）；新发现 10 条引擎缺口，其中只有 2 条需要核心补丁且都有可接受替代 |
| 00.4 | 事件可靠性 | **已完成** | `MjyPlatformBridge` 原型 12/12 测试通过（MariaDB 与 PostgreSQL），已经独立代码审查并修复严重/高/中问题；真实进程故障注入（SIGKILL）在两库均通过，结论为无需核心补丁（[ADR 0003](../adr/0003-runtime-events.md)）；投递中继已实现（HMAC 签名、按批确认、失败重试），插件测试 18/18 通过 |
| 00.5 | 租户隔离 | **已完成（含重大发现）** | 双实例 61 项越权尝试：57 项被拒，4 项因共享代码目录失败（[ADR 0002](../adr/0002-tenancy.md)、[越权矩阵](tenancy-negative-matrix.md)）；结论：隔离靠部署形态，不靠引擎代码 |
| 00.6 | 发布能力 | **已完成** | LSS 覆盖面、映射稳定性、激活后漂移面、激活失败回滚，MariaDB 与 PostgreSQL 均通过（[ADR 0005](../adr/0005-publishing.md)、[LSS 覆盖](lss-coverage.md)）；两条阻塞项见下 |
| 00.7 | 考试与配额 | **已完成（含重大发现）** | `MjyRuntimePolicy` 原型 24/24 测试通过，端到端 10 个场景全过（[ADR 0007](../adr/0007-exam-policy.md)、[考试与配额证据](exam-quota-evidence.md)）；**引擎自带配额没有原子性**：答卷表 262,144 行时 8 个并发抢 1 个名额，6～7 人同时抢到；平台名额租约（进场前预留＋配额行排它锁）3 轮 × 100 并发每轮恰好 1 人 |
| 00.8 | 私有化与成本 | **已完成** | 离线依赖清单、外连清单、隔离网络安装、备份恢复演练、成本模型、信创清单（[ADR 0008](../adr/0008-private-delivery.md)、[私有化交付](private-deployment.md)、[成本模型](cost-model.md)）；关键修正：每租户磁盘 749 MiB（ADR 0002 的 220 MB 漏算了每租户代码副本），只读代码树可行后 5000 租户由 3.54 TiB 降到 369 GiB |
| 00.9 | 供应商询证 | **清单已备好，待业务侧执行** | 按八类供应商列出必问问题、预填默认方案、判定标准与不合格替代路线；305 条中 98 条依赖外部接口、35 条依赖资质授权 |
| 00.10 | P0 总报告 | **已完成** | 结论：推荐架构成立、全程零核心修改，建议进入 P1；4 项前置条件（供应商询证、法务许可意见、仓库策略、冻结批准）见 07 号文件 |

## 许可风险（来自 00.2，需法务确认）

1. `application/commands/UpdateDbCommand.php` 文件头是 **AGPL-3.0-or-later**，是树里唯一的网络著佐权文件，直接影响 SaaS 形态；同文件 `@license` 标记写作 GPL v3，自相矛盾。
2. 根 `LICENSE` 只含 GPL-2 与 LGPL-2.1 文本，缺 GPL-3、LGPL-3、AGPL-3、Apache-2.0、MPL-1.1。
3. 运行时依赖 `greew/oauth2-azure-provider` 为 GPL-3.0-or-later，与引擎自称的 GPL-2.0-or-later 单向不兼容；`application/extensions/yii-jsoneditor` 为 Apache-2.0，同样与 GPL-2 单向不兼容。
4. `phpmailer` 为 LGPL-2.1-only（无 or later），升到 GPL-3 需要主动行使 LGPL-2.1 §3。
5. 压缩后的前端产物缺第三方版权声明（`adminbasics.js` 打包了 lodash 却只保留 LimeSurvey 版权行），MIT/BSD 要求保留。
6. `vendor/`（7,553 文件）与 `node_modules/`（3,055 文件）已提交进仓库，可能在仓库层面即已构成分发。
7. 我们自己的插件 `plugins/Mjy*` 继承 `PluginBase`、共享引擎进程与数据库连接，按衍生作品处理：**商业逻辑（配额、防作弊、计费、租户路由）不能放在插件里**，必须留在平台侧。
8. `tiamo/spss` 锁在 `dev-master`，`goldspecdigital/oooas` 锁在第三方 fork 的开发分支；CKEditor 4.22.1 是最后一个开源版本且 2023 年 6 月起停止维护（安全风险，非许可风险）。

完整证据与 13 条待法务确认的问题见 [sbom/licence-analysis.md](sbom/licence-analysis.md)。

## 事实订正

- **基线不是"7.1.2 发布版"**：上游没有 `7.1.2` 标签，最新 7.x 发布为 `7.1.1+260914`。我们的基线 `4c20c680` 是其后 108 个提交的 master 中间态（`version.php` 的 `buildnumber` 为空即其特征）。对外描述应为「基于上游 master 快照 `4c20c680`（版本号 7.1.2）」。

## 新增发现（将回写总方案）

1. 删除事件有两条绕过路径：后台批量删除 `ResponsesController.php:657`、插件 API `LimesurveyApi::removeResponse`，都用 `deleteByPk`。
2. response id 在问卷重新激活后会重复，事件自然键必须带“答卷表代次”（ADR 0003）。
3. 引擎在 CI 对齐配置下 unit 套件全部通过，此前的 79 错误/12 失败都来自环境配置。
4. 除 CI 列出的扩展外还需 `ext-calendar`。
5. **发布后可静默改题目编码**：`set_question_properties` 与后台 `QuestionAggregateService` 都不拦截已激活问卷的 `title` 修改，而字段名按数字 id 生成（`Q<qid>_S<sqid>#<scale>`），改编码不改答卷表任何列，数据库层面看不出异常，只有平台映射悄悄失配。对策：按归一化指纹周期比对（ADR 0005）。
6. **API 激活不做题目完整性检查**：`activate_survey` 只做 `checkHasGroup/checkGroup`，一道没有任何选项的单选题也能激活成功（后台界面会检查）。发布器必须自行校验。
7. **导入会自动改名非法或冲突的题目编码**，导入后必须重读 `get_fieldmap` 核对，不能信任导入返回值。
8. **隔离完全依赖部署形态**：多个实例共享同一份代码目录时，一个租户写入的 PHP 文件会被邻居实例执行，`security.php` 加密密钥共用，`allowed_hosts.php` 互相覆盖（已实测造成邻居 400）。每租户必须独立代码副本。
9. 引擎会话 cookie 名固定为 `PHPSESSID`，同域多实例会互相覆盖登录态，需按租户设置 `session.name`。
10. **共享代码目录还会污染测试**：功能套件里的主题测试被上一轮遗留在共享挂载中的文件搞红，与 00.5 的跨租户问题同源——每实例独立代码副本这条规则对测试环境同样适用。
11. 答卷附件的匿名访问保护仅来自 `.htaccess`，换 nginx 或 `AllowOverride None` 后，知道 sid 与文件名即可下载他人附件。
11. **引擎配额没有原子性**：`Quota::getCompleteCount()`（`Quota.php:165`）是不加锁的全表 COUNT，与 `em_manager_helper.php:5435` 写 `submitdate` 之间没有事务。常规数据量下偶发超发（14 轮里 1 轮 2 人）；答卷表 262,144 行时那条 COUNT 要 24 毫秒，8 个并发抢最后一个名额有 **6～7 人同时抢到**。硬名额必须由平台在进场前预留（ADR 0007）。
12. **`beforeSurveyPage` 可以拒绝一次提交**：它在 `SurveyIndex.php:228` 派发，早于处理 POST 的 `SurveyRuntimeHelper::run()`（同文件 `:692`），钩子里调 `renderExitMessage()` 会终止请求，答案与 `submitdate` 都不落库。`afterSurveyQuota` 做不到——它派发时答案已经存过了。RemoteControl `add_response` 则完全绕开这个钩子。
13. **PHP 会话 id 不是稳定的作答者身份**：`resetAllSessionVariables()`（`frontend_helper.php:1406`）调 `regenerateID(true)`，同一个作答者的 GET 与 POST 会话 id 不同。凡是"按作答者"的计数（名额、计时、防重复）都不能拿会话 id 当键，必须用平台发的 token。
14. **答卷行在打开问卷时就创建**，不是交卷时创建；"有没有作答"只能看 `submitdate`，不能看行数。
15. **题目级 `time_limit` 是纯前端 JS 倒计时**（`qanda_helper.php:394` 起），服务端不做任何校验；问卷级 `expires` 虽是服务端判定，但全体考生共享一个到期时刻。考试限时必须由平台按场次下发（ADR 0007）。
16. **扩展题型的服务端校验只能靠改写 `$_POST`**：引擎没有「否决单道题的作答并把人留在本页」的事件。`beforeSurveyPage`（`SurveyIndex.php:228`）是唯一早于 EM 处理 POST 的落脚点，插件在那里把非法值清空，借必答校验拦人。**非必答题拦不住**（ADR 0006 限制 1）。
17. **EM 看到的自由文本值是实体编码后的**：`GetVarAttribute()` 对 `S`/`T`/`U`/`Q`/`;`/`D` 走 `htmlSpecialCharsUserValue()`（`em_manager_helper.php:8950`），其中 `{`→`&#123;`、`}`→`&#125;`（同文件 `:10307`）。想用 `preg` / `em_validation_q` 匹配 JSON，正则必须写成编码后的形状。另外：`man_message` 会被问卷模板以 `raw` 渲染，凡是把作答者原文放进提示文案的插件都会造成反射型 XSS。
18. **上传文件改名不派发事件**：页面提交时引擎把 `futmp_<随机>` 改成全新的 `fu_<随机>`（`em_manager_helper.php:8809`），中间没有任何钩子，临时文件名不能当平台资产标识。
19. **题型主题缺失时导入静默降级**：`Question::questionThemeNameValidator()`（`Question.php:1531`）把未安装的 `question_theme_name` 换成基础主题，不报错也不写 `importwarnings`。题型主题必须随镜像预装并在发布后回读校验。
20. **题型主题的 `answercolumndefinition` 在 7.1.2 里是死代码**：`createFieldMap()` 的守卫判断 `$arow['attribute']`（`common_helper.php:1748`），而主查询（同文件 `:1705`）没有这一列，主题声明的答卷列类型永远不生效。
21. **`update_response` 绕过全部校验**：`remotecontrol_handle.php:3535` 直接 `SurveyDynamic::encryptSave()`，不跑 EM、不跑必答、不跑插件闸门。平台侧的答卷编辑入口必须自己校验。
