# P0 技术验证进度

- 批准：用户于 2026-09-18 批准 P0；决定不使用代理，所有内容放在当前仓库（ADR 0004）。
- 仓库：`lsgoodlionel/star-survey`，分支 `main`（根提交为 LimeSurvey 7.1.2 快照，上游 `4c20c680`；原 fork 保留为 remote `limesurvey-fork`）。
- 周期：6 周（W1 = 2026-09-18 起）。

| 编号 | 任务 | 状态 | 证据 / 下一步 |
|---|---|---|---|
| 00.1 | 需求冻结 | 未开始 | 需产品负责人参与：U-01～U-11 与四维清单 |
| 00.2 | 引擎认证与测试基线 | **进行中** | CI 对齐测试栈完成：MariaDB 848 用例、PostgreSQL 16 840 用例，均 0 错误 0 失败（[ADR 0001](../adr/0001-engine-baseline.md)）；依赖与许可清单已完成（[sbom/](sbom/)）：109 个 PHP 包、22 个已提交的前端运行时包，0 个未声明许可、0 条安全告警，但发现 GPL-3/LGPL/AGPL 混用问题；待：upstream 差异（受网络限制）、功能套件 |
| 00.3 | 题型纵切 | 未开始 | 依赖 00.6 的 LSS 字段覆盖结论 |
| 00.4 | 事件可靠性 | **已完成** | `MjyPlatformBridge` 原型 12/12 测试通过（MariaDB 与 PostgreSQL），已经独立代码审查并修复严重/高/中问题；真实进程故障注入（SIGKILL）在两库均通过，结论为无需核心补丁（[ADR 0003](../adr/0003-runtime-events.md)）；投递中继已实现（HMAC 签名、按批确认、失败重试），插件测试 18/18 通过 |
| 00.5 | 租户隔离 | **已完成（含重大发现）** | 双实例 61 项越权尝试：57 项被拒，4 项因共享代码目录失败（[ADR 0002](../adr/0002-tenancy.md)、[越权矩阵](tenancy-negative-matrix.md)）；结论：隔离靠部署形态，不靠引擎代码 |
| 00.6 | 发布能力 | **已完成** | LSS 覆盖面、映射稳定性、激活后漂移面、激活失败回滚，MariaDB 与 PostgreSQL 均通过（[ADR 0005](../adr/0005-publishing.md)、[LSS 覆盖](lss-coverage.md)）；两条阻塞项见下 |
| 00.7 | 考试与配额 | 未开始 | `MjyRuntimePolicy` 原型、100 并发最后名额 |
| 00.8 | 私有化与成本 | 未开始 | 已记录网络问题：GitHub clone、Docker Hub、Packagist 均出现超时，需要内网镜像源方案 |
| 00.9 | 供应商询证 | 未开始 | 需业务侧发起，不采购、不签约 |
| 00.10 | 预算排期更新 | 未开始 | P0 末 |

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
10. 答卷附件的匿名访问保护仅来自 `.htaccess`，换 nginx 或 `AllowOverride None` 后，知道 sid 与文件名即可下载他人附件。
