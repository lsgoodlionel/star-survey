# Wave 2 模型切换交接报告

日期：2026-10-12。用户要求暂停开发、生成报告并准备切换模型。

## 1. 结论与真实断点

- 当前阶段是模板与品牌闭环 Wave 2，停在 **Task 2：品牌资产契约及真实发布/预览验收**。
- Java、Python、PHP 和生产渠道密钥相关实现已在工作区，但尚未完成独立复审与完整验收，**不得标记 Task 2 或 Wave 2 完成**。
- Task 3 类型化前端 API、Task 4 模板中心、Task 5 生命周期、Task 6 品牌语言编辑器、Task 7 浏览器纵向验收、Task 8 生产交付均未在本阶段完成。
- 用户之前看不到新增前端入口的直接原因仍然存在：本批主要是发布链路实现，不能描述为前端已交付。
- 本次按用户要求收拢工作，不自动进入下一任务、不发布生产、不提交或推送未验收代码。

## 2. 仓库与权威文档

- 工作目录：`/Users/lionel/Develop/survey/.worktrees/admin-web`。
- 分支：`feat/admin-product-alignment`。
- 基线 HEAD：`0becfd7d63f01f09c701552d81ea84b2130494c1`。
- origin：`https://github.com/lsgoodlionel/star-survey.git`。另有 `limesurvey-fork`，不得向该上游 fork 推送本次产品代码。
- 活动计划：`docs/superpowers/plans/2026-10-11-template-branding-wave2.md`。
- 设计：`docs/superpowers/specs/2026-10-11-template-branding-wave2-design.md`。
- 父路线：`docs/superpowers/plans/2026-10-09-platform-productization-production-upgrade.md`。
- 历史报告：`docs/audits/2026-10-09-product-alignment/14-full-development-report.md`。
- Harness：`docs/agent/HARNESS_HOST.json` 的 activePlan 已指向 Wave 2；不要重新初始化或覆盖旧交付证据。
- 细项账本：`.superpowers/sdd/2026-10-11-template-branding-wave2/progress.md`，可能被 Git 忽略。
- 先读 `AGENTS.md`、`docs/agent/PROJECT_MEMORY.md`、保护路径、质量门矩阵及迁移记忆；实际状态优先于旧文档。

## 3. 工作区实现范围（未提交）

### Java 平台

- `SurveyAssetLinks` 从当前租户的固定资产版本读取字节，验证 MIME、大小、存储摘要和物化快照摘要；关闭输入流。
- `SurveyAssetSource`、`GatewayRequest.BrandAsset` 增加有限大小的品牌资源 bundle。
- `SurveyPublishService`、`PublishReconciler`、`PreviewSessionService` 在发布、恢复和预览边界传递或重建固定版本字节。
- `HttpPublishGatewayClient` 保持没有 brandAssets 时的旧请求形状。
- 资产、预览、HTTP 客户端、模板生命周期测试已有相关覆盖。

### Python 发布网关

- 新增 `pubgw/brand_assets.py`：逐项绑定槽位、文件名、MIME、大小、SHA-256，导入得到 sid 后签名调用 PHP 端点。
- 发布和预览接入资源安装，失败回滚删除新建问卷；预览恢复保存并重用 bundle。
- 未携带 brandAssets 的历史请求保留旧指纹，避免历史幂等记录失配。
- 物化 URL 不再硬编码 survey.example；仍限制规范 HTTPS 与固定路径，仅作身份元数据，网关不会抓取 URL。
- 含品牌图片的主题选项不在 import 前写入 LSS，避免 LimeSurvey 导入时因图片尚不存在而丢弃。
- 只扩大发布与预览端点请求体上限，其他端点保持原边界。

### PHP 引擎桥接

- 新增 `plugins/MjyQuestionExtensions/MjyBrandAssetEndpoint.php`，先检查封闭查询字段与 HMAC，再读取/验证 JSON 与字节。
- 只允许固定图片名及受限 zh-business 选项；落在 `upload/surveys/<sid>/images/`，通过 `image::survey::<name>` 配置问卷图片。
- 写入或主题选项失败执行文件回滚；日志仅记录异常类型，不记录原始异常消息。
- `MjyQuestionExtensions.php` 注册新端点。
- `application/core/LSHttpRequest.php` 与 `application/config/internal.php` 新增精确路由 CSRF 豁免，保留原有前缀语义；必须复核该有限上游改动的批准范围。
- 新增端点测试及 CSRF 精确匹配测试。本轮修正测试中的 `\CLogger` 命名空间引用。

### 生产配置

- `surveyctl.py` 根据原始实例秘密及 engine id 派生独立 channel secret，安装/升级时原子写入并限制文件权限。
- Compose 的 gateway 使用派生秘密，原始实例秘密只给引擎，不再把二者混用。
- 增加固定向量、安装/升级/幂等与 Compose 测试；fixtures 中新增的是测试数据，不是真实凭据。
- 两个已暂存的 AI health shell 文件仅为 `100644 -> 100755` 权限修正。

## 4. 验证证据与边界

| 范围 | 结果 | 证据边界 |
|---|---|---|
| 网关全量 unittest | 本轮重跑 1,232 项通过 | 由 run-brand-theme.sh 调用，不能代替真实新资源链路 |
| 既有品牌/双语真实引擎 | 本轮 MySQL E2E 通过，failures=[] | legacy logoFile，覆盖主题、品牌文本、多语言、回退、转义和无第三方来源；不是新 brandAssets E2E |
| Java 定向测试 | Surefire 报告核对：62 项，0 failure/error/skip | 上一连续开发片段实际通过；本轮读报告复核，非全量 Java 门禁 |
| 生产单元测试 | 上一片段 105 项 OK，其中 1 项 opt-in 真机测试 skipped | 不等于单机生产部署/恢复已验收 |
| PHP lint/行为 smoke | 上一片段通过 | smoke 使用替代测试基类，不能冒充 PHPUnit |
| 真实 HTTP 未认证请求 | 上一片段精确端点 401，子路由 400 | 没有证明带合法签名的图片写入成功 |
| PHPUnit | 尚未执行完成 | 依赖现已独立安装成功，可继续执行 |
| 新图片真实发布/预览 | 尚未通过验收 | 新脚本正在交接，不能标记完成 |
| git diff --check | 本轮通过 | 不替代功能测试 |

Java 报告分布：SurveyAssetPublishTest 19、SurveyBrandAssetBundleTest 2、PreviewSessionApiTest 11、HttpPublishGatewayClientTest 15、SurveyTemplateLifecycleTest 15。

定向 Java 复跑命令（独立测试库，禁止修复共享库的 Flyway 历史）：

```bash
PLATFORM_DB_NAME=platform_task2_brand platform/deploy/platform-dev/mvn.sh test -Dtest='cn.mjy.platform.asset.SurveyBrandAssetBundleTest,cn.mjy.platform.asset.SurveyAssetPublishTest,cn.mjy.platform.survey.preview.PreviewSessionApiTest,cn.mjy.platform.survey.gateway.HttpPublishGatewayClientTest,cn.mjy.platform.survey.template.SurveyTemplateLifecycleTest'
```

其他命令：

```bash
platform/deploy/test/run-brand-theme.sh
python3 -m unittest discover -s platform/deploy/production/tests -q
# 在 platform/tools/publish-gateway 目录：
python3 -m unittest discover -s tests -t . -q
```

## 5. 环境和恢复注意事项

- `survey-test-web` 和 `survey-test-db` 是当前 MySQL 测试栈；保留运行，没有公开宿主机 Web 端口，不提供新的人工访问 URL。
- `platform-db` 是既有 PostgreSQL。共享 platform 库有历史 Flyway V111 校验不一致，未 repair；本次 Java 使用独立 `platform_task2_brand` 库。
- 宿主机没有 PHP；测试容器有 PHP。仓库 vendor 没有 PHPUnit。
- 先前根目录 composer install 遇到下载/解包问题，曾影响 tracked vendor；仅本任务造成的 vendor 改动已恢复。本轮 `git status --short vendor` 为空。
- 本轮已终止遗留 Composer 和 Git 下载子进程；`docker exec survey-test-web ps -eo pid,args` 仅剩 Apache 和检查进程。
- 本轮在 **容器内 `/tmp/task2-phpunit`** 隔离安装仓库已声明的 PHPUnit/WebDriver/DbUnit 测试依赖成功，未改仓库 composer.json/lock/vendor。PHPUnit 为 9.6.38。
- 后续可用 `/tmp/task2-phpunit/vendor/bin/phpunit`，同时确保其 autoload 与真实 `tests/bootstrap.php`、插件类加载结合正确。不要以空 PHPUnit 类绕过测试。
- `/private/tmp/task2_php_smoke.php` 与容器 `/tmp/task2_php_smoke.php` 仅为上一片段的临时行为 smoke，不是正式验收。
- 真实签名 HTTP 成功测试需要专用测试实例环境；当前共享引擎没有所需渠道秘密配置，不得把真实生产凭据带进测试。
- 不重新执行根目录 Composer 安装，不清理或回滚未提交产品代码，不重置用户工作区。

## 6. 待验证风险与复审重点

以下为后续需要验证的风险，不是已经证实的漏洞：

1. 混合使用新 logoAsset 与旧 faviconFile（或反向）时，PHP 端点是否误拒绝没有对应 bundle 槽位的 legacy 文件。
2. handleHttp 的 body reader 使用完整读取，需确认在已验签 bodySize 下仍有实际读取上限，防止实现与“有界读取”描述不符。
3. 模板图片选项在真实 DB 保存后是否保留，并由实际作答页解析为可访问图片；不能只断言文件存在。
4. 发布、预览重试、安装失败回滚、历史版本图片固定，需同时验证真实调用顺序及用户可见结果。
5. 精确 CSRF 路由规则、生产派生密钥及未带 bundle 的历史指纹需独立最终复审。

## 7. 按顺序接续

1. 核对工作区和本报告，读取子 Agent 交接，禁止将未完成脚本视为通过。
2. 用隔离 PHP 依赖运行新增端点测试和 CsrfHttpRequestTest，解决真实失败。
3. 完成新图片 bundle 的发布、预览、失败回滚和版本固定 E2E；数据库相关场景按项目要求补 MySQL/PostgreSQL 证据。
4. 针对剩余风险复审并修复；更新契约和真实进度。只有实际通过才能关闭 Task 2。
5. Task 3：模板/资产/品牌 typed clients 与测试。
6. Task 4：模板浏览、详情、复制及真实前端入口。
7. Task 5：租户/平台模板生命周期与权限。
8. Task 6：品牌/语言编辑、资产选择上传、乐观锁和上下文保留。
9. Task 7：真实浏览器全旅程、响应式、无障碍和隔离。
10. Task 8：生产 Compose、备份恢复、三路独立复审及代码/文档/GitHub/Obsidian 同步。

## 8. 交付同步状态

- 本批实现尚未提交，未推送，未运行其 GitHub CI，未同步 Obsidian。
- 本报告是本地中断交接，不是完成声明或发布记录。
- 用户已持续授权正常分支提交和普通 push，但测试未闭环时仍不得推送；禁止 force push、自动合并或改写历史。
- 后续完成验证后先核对 `~/.codex/memories/claude-code-migration/github-repositories.md`，再按职责拆分提交并更新对应 Obsidian 项目笔记。

## 9. 子 Agent 收拢结果

- Singer，agent id `01a12d82-2b7a-7e12-be47-de50f176b6b0`，已返回报告并关闭。
- 创建时未指定 model（继承父模型），指定 reasoning_effort=high；精确部署模型 ID 未由工具结果提供，不编造该字段。
- 新增四个未跟踪文件：`platform/tests/e2e/brand_assets.py`、`platform/tests/e2e/test_brand_assets.py`、`platform/deploy/test/run-brand-assets.sh`、`platform/deploy/test/compose.brand-assets.yml`。
- 子 Agent 报告先观察 10 项 RED，再实现并取得 10/10 GREEN；网关 1,232 项通过、runner shell 语法通过。主 Agent 尚未独立复跑这四个新文件。
- 隔离 Docker 首跑未进入业务场景，错误为 `isolated compose cleanup failed (raw output suppressed)`；提权重跑被模型切换请求中断。
- 接续先修复 runner 的诊断问题：finally 清理失败不能覆盖原始启动异常。随后授权运行 `bash platform/deploy/test/run-brand-assets.sh --db mysql`，通过后补 pgsql/all。
- 子 Agent 检查没有遗留 runner 进程、`brand-assets-*` 容器或网络；共享测试栈没有被其修改。主 Agent 的 PHPUnit 安装和 legacy E2E 会话也已正常结束。

## 10. 2026-10-24 接续验收记录（仍未关闭 Task 2）

- 真实工作树：`/Users/lionel/Develop/survey/.worktrees/admin-web`，分支 `feat/admin-product-alignment`，HEAD `34d08c3295525adaa143905c8ead10a7bf05b2cf`。既有未提交文件和两个已暂存的 AI health 执行位均保留；本轮没有提交或推送。
- Docker Desktop 重启后，既有 `survey-test-db` 是空测试库，已用测试配置初始化。容器内隔离 PHPUnit 9.6.38 搭配真实 `tests/bootstrap.php` 执行：品牌端点 10 项、41 assertions；CSRF 5 项、18 assertions，全部通过。新增测试显式加载真实插件类，未使用替代 PHPUnit 基类。
- 新旧槽位混用先观察真实 RED（新 logo + 旧 favicon 返回 400），随后修复 PHP 端点，双方向混用测试通过。`handleHttp` 现只读取最多 `MAX_BODY_BYTES + 1` 字节；PHP lint 通过。
- Gateway 全量 unittest 1,232 项通过；Java 在独立 `platform_task2_brand` 库离线运行 5 个定向类，共 62 项通过；production 单测 105 项通过，其中既有 opt-in 真机项 skipped=1。首次默认沙盒运行 Gateway/production 因本地端口和 Docker socket 权限失败；同一命令在获授权环境重跑通过。未修改共享 `platform` 库 Flyway 历史。
- 隔离 MySQL E2E 已验证真实 RC import、签名 PHP 写入、主题 DB 选项、作答页 logo/favicon HTTP 字节、第二次发布不同图片和旧版本 URL 字节固定。作答页 favicon 经 LimeSurvey 资源发布器变为同源 `/tmp/assets/<digest>/...`；runner 已按固定文件名、限定哈希目录、同源和实际返回字节验证。
- 预览关闭阶段两轮验收断言仍未闭环：第一轮把已激活预览误判为应删除 SID，第二轮把作答页打开后未提交的记录误判为污染。现有网关契约是保留隔离 SID 并设置过期。第二轮后已将断言改为检查已过期、预览访问 410 和零已提交答卷，**该改动尚未重跑**；按同类问题两次处理上限停止自动修复。MySQL 完整场景、PostgreSQL 场景、失败回滚和预览重试均不得宣称通过。
- E2E runner 的清理失败已不再覆盖原始异常；最近运行后未见遗留 `brand-assets-*` 容器。下一任务应先独立复核预览隔离验收语义及该未验证断言，再决定是否授权第三轮运行；之后才继续 PostgreSQL、完整 Gate 和独立复审。当前不关闭 Task 2，不进入 Task 3，不更新 Accepted 口径。

## 11. 2026-10-24 继续开发后的回滚阻断

- 用户要求核对 Wave 2 plan 与产品蓝图，继续主线开发。仓库、分支、HEAD 与上一节相同，工作区和两个暂存执行位均保留。
- 经源码核对，已激活预览关闭应保留独立 SID 并写入过期时间；打开页面可能留下未提交答卷。MySQL 第三轮真实 E2E 通过预览崩溃恢复、同 SID 重试、签名图片安装、作答页取图和关闭隔离检查。
- 同一轮在错误 channel key 的安装失败回滚处失败：`delete_survey` 已删除问卷，但 `lime_template_configuration` 仍存在该 SID 的配置行。`application/models/Survey.php` 的 `delete()` 删除问卷数据及上传目录，但没有删除 `TemplateConfiguration` 行；RemoteControl 返回成功并不足以证明品牌选项清除。
- 为聚焦品牌残留，验收脚本改为核对失败 SID 无任何 `mjybrand*` 选项，且上传目录已删除；第二轮 MySQL 仍报 `failed installation left brand theme options behind`。因此这不是单纯的默认主题行残留，品牌配置确实未完成回滚。两次同类处理后按项目规则停止自动修复，未运行 PostgreSQL。
- 当前可确认的 MySQL 证据：发布 v1/v2、数据库主题选项、作答页 logo/favicon HTTP 字节、旧版本字节固定、预览崩溃恢复与关闭隔离。**失败回滚未通过，Task 2 仍进行中**；不得提交推送、进入 Task 3 或提升能力 Accepted 状态。
- 下一次处理需先由人工或新任务决定如何在不改上游引擎源码的前提下删除失败 SID 的品牌主题配置，并同时覆盖 MySQL/PostgreSQL。保留当前失败断言，不通过放宽检查宣称回滚成功。

## 12. 2026-10-26 主线接续与重复失败

- 用户要求按蓝图与计划顺序完成既有前后端功能。真实分支、HEAD 和未提交工作区保持上一节状态；仍未提交或推送。
- 针对失败回滚品牌残留，网关新增按 `brandAssets` bundle 延后写入品牌选项的编译路径：原始编译产物保留完整品牌定义，只有导入 LSS 在签名图片安装前不带新图片槽位的颜色、标题等选项。无 bundle 的旧定义不变；混合旧文件槽位保留其导入选项。新增 compiler 与 Publisher 聚焦测试已通过，且先观察 Publisher 测试 RED。
- 隔离 MySQL E2E 两轮仍到达发布 v1/v2、旧版本图片固定、预览崩溃恢复与关闭通过，随后在失败回滚主题配置读取处报同一 `JSONDecodeError`。第一次将空字符串选项按空对象处理后，第二次同指纹仍失败；`Database.rows()` 使用 MariaDB 批量文本输出，异常可能来自 NULL 或非 JSON 表示，但**尚无足够证据断言具体值**。品牌残留是否已被新编译路径消除，尚未完成真实验收。
- 根据两次同类修复上限停止自动修复；未进入 PostgreSQL、Task 3 或后续 UI。必须在新任务或人工诊断中先取得脱敏的选项值类型/长度与数据库 NULL 状态，再修复读取或真实残留，保持“无 `mjybrand*`、上传目录已删除”的验收要求。完整双库与最终 Gate 通过前不得提交推送或提升能力状态。
- 本轮变更后 Gateway 全量 unittest 1,234 项通过；`git diff --check`、暂存差异检查和 Python 语法编译通过。这些通过项不替代未完成的真实双库回滚验收。

## 13. 2026-10-26 再次接续的验收诊断

- 用户回复“继续”，本轮只处理 Task 2 回滚验收。只读 SQL 已确认 `lime_template_configuration.options` 是可 NULL 的 text 列，MariaDB 批量客户端将 SQL NULL 输出为字面量 `NULL`；但 `COALESCE` 后真实 MySQL E2E 仍在 JSON 解码处失败，不能把 NULL 单独认定为全部根因。
- 第二次将验收探针改为 SQL 中计数 `options LIKE '%mjybrand%'`，MySQL 发布 v1/v2、旧版固定、预览恢复继续通过，回滚处报品牌标记存在。随后只读检查 `themes/survey/zh-business/config.xml` 发现该主题自身就定义 `mjybrand*` 默认键，其中 `mjybrandlogo` 默认为 `off`；因此单纯关键词计数无法区分默认键与作者品牌值，**不能据此确认新编译路径仍残留作者数据**。
- 本轮两次修复仍未得到可靠回滚断言，按项目上限停止自动修复与 MySQL 重跑；未运行 PostgreSQL 或 Task 3。下一次应在隔离库中以脱敏方式核对失败 SID 的选项是否含特定非默认品牌值或 `image::survey::` 资源引用，再建立跨 MySQL/PostgreSQL 的精确断言；不得把默认主题键当成回滚失败，也不得放过作者值残留。
- 两次隔离运行后无 `brand-assets-*` 容器遗留；`git diff --check` 和暂存差异检查通过。代码、文档、既有暂存与未跟踪文件均保留，未提交推送。

## 14. 2026-10-27 Task 2 回滚闭环与双库验收

- 接管同一工作树与 `feat/admin-product-alignment` 分支，起点 HEAD `34d08c3295525adaa143905c8ead10a7bf05b2cf`；`origin` 是 `lsgoodlionel/star-survey`，上游 `limesurvey-fork` 不接收产品代码。既有两项已暂存 AI health 执行位未纳入 Task 2。
- 隔离 MySQL 定点只读诊断先确认：单独发布失败时问卷已删除、主题配置行可能保留但不含作者品牌值；完整前序流程的发布失败同样无作者值，预览失败却保留测试作者颜色、标题与替代文本。根因是预览导入未使用发布已有的 `defer_branding`，失败安装后引擎删除问卷仍可能留下导入的主题选项。修复后，发布和预览均在签名安装成功时才写作者品牌选项。
- 回滚验收不再把 `zh-business` 的默认 `mjybrand*` 键或 `TemplateConfiguration` 行本身当作作者残留；跨 MySQL/PostgreSQL 查询精确检查测试作者颜色、标题、替代文本、固定文件名及任何 `image::survey::` 引用，并同时要求失败 SID 的问卷与上传目录消失。测试先观察到旧断言误报及预览真实作者残留，修复后通过。
- 独立复审发现新旧图片槽位混用时 PHP 会清除未替换的旧图片选项。新增双方向配置合并回归测试，PHP 端点现在只替换本次新资源对应的图片槽位；复审确认无剩余 Task 2 阻断问题。
- 最终门禁：隔离 MySQL 与 PostgreSQL E2E 各通过发布 v1/v2、作答页图片字节、旧版本固定、预览崩溃恢复/关闭、错误 channel key 的发布及预览回滚；两栈均清理且共享测试栈未变化。Gateway 全量 1,234 项通过；Java 5 类共 62 项、0 failure/error/skip；PHP 品牌端点 11 项/49 assertions、CSRF 5 项/18 assertions；production 105 项通过（既有 opt-in 1 项 skipped）；旧品牌主题 MySQL E2E 通过；`git diff --check` 通过。
- production 测试改用明确的全零示例密钥夹具。原有未跟踪的 64 位十六进制同名密钥文件来源未确认，保留在本地且不提交。Task 3–8、完整前端旅程、生产部署与 Wave 2 六层最终验收仍待后续任务；不提升整体 capability/Accepted 状态。
