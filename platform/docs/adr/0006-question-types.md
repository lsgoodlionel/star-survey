# ADR 0006：扩展题型的承载方式

- 状态：三条纵切原型已实现，MariaDB 10.11 与 PostgreSQL 16 端到端全通过；题型工厂待实现
- 日期：2026-09-20
- 关联：P0-00.3；依赖 [ADR 0005](0005-publishing.md) 的发布链路与 [ADR 0003](0003-runtime-events.md) 的代次约定；总方案 §4.1、WP-02
- 配套证据：[P0-00.3 题型纵切](../p0/question-slice.md)、`platform/tests/e2e/question_slice.php`（＋`question_slice_support.php`）、`plugins/MjyQuestionExtensions/tests/`

## 背景

WP-02 要交付的题型里，有三种形态决定了整套方案的上限，P0 必须先用引擎实测定死：

1. **原生数组题**：引擎自带，用来确认「不扩展也够用」的那部分到底覆盖多少；
2. **自增表格**：作答者自己加行的表格。引擎**没有**这个题型，也没有可以承载它的列结构；
3. **录音 / 附件题**：引擎有文件上传题，但平台需要自己的资产 id、自己的存储生命周期。

这三条如果不能走完「起草 → 编译导入 → 激活 → 真实 HTTP 作答 → 断点续答 →
改已提交答卷 → 导出 → 重新导入 / 主题升级」的完整链路，WP-02 的工期与范围就是空的。

### 引擎给扩展题型留了哪些口子

| 口子 | 位置 | 能做什么 |
|---|---|---|
| 题型主题（question theme） | `themes/question/<name>/survey/questions/answer/<folder>/` | 换掉某个基础题型的 twig、挂 CSS/JS、声明题目属性 |
| `newQuestionAttributes` 事件 | `application/models/QuestionAttribute.php:569` | 注册题目属性定义（后台编辑界面、`xssfilter` 开关） |
| `beforeQuestionRender` | `application/helpers/SurveyRuntimeHelper.php:1888` | 改题干、必答提示、答案区 HTML |
| `beforeSurveyPage` | `application/controllers/survey/SurveyIndex.php:228` | 在引擎处理 POST **之前**动 `$_POST` |
| `beforeProcessFileUpload` | `application/controllers/UploaderController.php:190` | 文件落到临时目录时插一脚 |
| `preg` / `em_validation_q` 题目属性 | `application/helpers/expressions/em_manager_helper.php:2648` | 服务端跑一个正则 / 表达式，不通过就不许翻页 |

**引擎没有留的口子**（这是本 ADR 的核心约束）：

- 没有任何事件可以**否决单道题的作答并把人留在本页**。`afterResponseSave`
  在写库之后才触发，`afterSurveyComplete` 更晚。`beforeSurveyPage` 里调
  `renderExitMessage()` 可以整体终止这次请求（P0-00.7 的发现 12），
  但那是"把人踢出问卷"，不是"这一题填错了请重填"，做不了逐题校验。
- 没有「新增题型」的扩展点。`lime_question_themes.question_type` 只能是引擎已知的
  29 个单字符类型之一，答卷表的列结构由 `createFieldMap()` 按类型硬编码生成。

## 决定

### 1. 三档承载方式

| 档位 | 做法 | 适用 |
|---|---|---|
| A 原生 | 直接用引擎题型，平台只做 DSL 映射 | 单选、多选、数组、文本、数值、日期、排序、上传等 |
| B 主题 | 原生题型 ＋ 题型主题（换 twig／挂 JS／加属性） | 外观与交互不同、**作答数据形状不变** |
| C 副表 | 基础题型存整块 JSON ＋ 插件校验 ＋ 结构化副表 | 数据形状引擎表达不了（自增表格、动态矩阵） |

**只有 C 档需要插件。** B 档纯主题的题目在插件被停用时只是退化成基础题型，数据不丢。

### 2. 自增表格＝长文本（type `T`）＋ 题型主题 ＋ 结构化副表

- 基础题型选 `T`：答卷表里是一列 `text`，整张表以 JSON 信封
  `{"v":1,"rows":[{...},...]}` 存进去。引擎的保存、断点续答、导出、答卷编辑
  全都按普通长文本处理，一行核心代码都不用改。
- 列定义放题目属性 `mjy_table_columns`（JSON），行数上下限放
  `mjy_table_min_rows` / `mjy_table_max_rows`。三者都由插件通过
  `newQuestionAttributes` 注册，随 `.lss` 的 `question_attributes` 小节发布。
- 题型主题 `mjy-repeating-table` 只负责编辑体验：把 JSON 展开成可增删行的表格，
  改动后写回 textarea。**关掉 JavaScript 时 textarea 直接可见**，作答者仍能提交。

### 3. 服务端校验是两道闸门，插件那道是权威

浏览器端的任何结论都不可信，因此：

- **闸门一（引擎原生，`preg`）**：结构闸门，只管「是不是这个信封」。
  优点是插件停用时仍然生效；缺点见「已知限制」里的实体编码坑。
- **闸门二（插件，`beforeSurveyPage`）**：语义闸门，按列规格逐格校验，
  并把合法值**重新归一化**后写回 `$_POST`（列顺序按规格排、去空白、重新编码），
  所以入库的永远是规范形式，不是浏览器发来的原文。

因为引擎没有「否决提交」的事件，闸门二不合法时的动作是**把 `$_POST` 里的值清空**，
借引擎自己的必答校验把作答者留在本页，同时用 `beforeQuestionRender` 把真正的
拒绝理由显示出来。这是引擎现状下最便宜的可行方案，代价是题目必须设为必答
（见「已知限制」）。

闸门二的三条安全约束（独立代码审查后加固）：

1. **拒绝文案里不得出现作答者原文。** `man_message` 会被问卷模板以 `raw` 渲染
   （`themes/survey/*/views/subviews/survey/question_subviews/valid_message_and_help.twig`），
   而 `CHtml::tag()` 不转义内容。未知列名完全由作答者决定，因此在校验器里
   就压成 `[A-Za-z0-9_-]{0,32}`，再在渲染处 `CHtml::encode()` 一次（双重防线）。
2. **行数与报文长度有代码级硬上限**（`HARD_MAX_ROWS=500`、`MAX_PAYLOAD_LENGTH=256 KiB`），
   题目属性缺失时回落到默认 20 行而不是「不限行」——**失败关闭**。
   否则一次请求可能产生上万条单元格插入。
3. **一道题的配置坏掉只拦这道题。** 每道题单独兜异常，绝不让一个坏掉的列定义
   把整页其余题目的校验一起跳过（失败开放是最糟的失败方式）。

### 4. 副表契约

两张表，自然键与 [ADR 0003](0003-runtime-events.md) 的事件表完全一致，
可以直接同库反连接对账：

```
{prefix}mjyquestionextensions_answer_cell     一个单元格一行
  (engine_instance_id, survey_id, generation, response_id, question_code,
   row_index, column_code)  ← 唯一索引
  cell_value text, updated_at datetime

{prefix}mjyquestionextensions_answer_state    一次作答一行
  (engine_instance_id, survey_id, generation, response_id, question_code)  ← 唯一索引
  row_count int, is_valid int, errors text, updated_at datetime
```

三条刻意的选择：

1. **键用题目代码 `question_code`，不用字段名。** LimeSurvey 7 的答卷列名是
   `Q<qid>`，只由数字主键决定（`application/helpers/common_helper.php:1759`），
   跨实例、跨导入批次都会变（ADR 0005 场景二）。字段名只在本次激活期内现算。
2. **带代次 `generation`。** 停用再激活会重建 `responses_<sid>`，答卷 id 从 1 重新计数。
   代次直接读 `MjyPlatformBridge` 的 `{prefix}mjyplatformbridge_generation`，
   两个插件共用同一个值，不另起一套。
3. **状态表与单元格表分开。** 校验不通过时单元格表**清空**、状态表记
   `is_valid=0` 与错误原因：引擎答卷表里还留着原始文本，平台必须能一眼看出
   「这条不可信」，而不是看到一张空表就以为作答者没填。

上传会话表同一套键，另加平台资产 id：

```
{prefix}mjyquestionextensions_upload_session
  upload_token (uuid, 唯一) = 平台侧资产 id
  (engine_instance_id, survey_id, generation, response_id, question_code)
  field_name, original_name, temp_name, stored_name, extension,
  size_bytes, state('received'|'bound'), created_at, bound_at
```

### 5. 录音／附件题＝原生上传题（type `|`）＋ 上传会话

`beforeProcessFileUpload` 时开一条会话拿到 `upload_token`，答卷保存后
（`afterResponseSave` / `afterSurveyDynamicSave`）按「原始文件名＋大小」
把会话绑到最终文件名上。之所以不能用更强的标识，见「已知限制」第 3 条。

### 6. 题型主题必须随引擎镜像预装，发布前校验

`.lss` 只带 `questions.question_theme_name` 这个字符串，不带主题本身
（[LSS 覆盖面](../p0/lss-coverage.md)）。目标实例没装该主题时，引擎会
**静默**把题目换成基础主题，既不报错也不写 `importwarnings`
（`application/models/Question.php:1531-1556`）。所以发布网关必须：

1. 生成 `.lss` 前校验所有 `question_theme_name` 在目标实例的
   `lime_question_themes` 里存在；
2. `import_survey` 之后回读题目行，确认主题名没被换掉，
   与 ADR 0005 的代码集合校验一起做。

## 验证

### 插件单元测试

`plugins/MjyQuestionExtensions/tests/`，35 个用例在 MariaDB 10.11 与
PostgreSQL 16 上全部通过（每轮先确认 RED）：

| 文件 | 覆盖 |
|---|---|
| `MjyRepeatingTableValidatorTest.php` | 信封版本、坏 JSON、`rows` 必须是数组、行数上下限、未知列、必填、整数与区间、按字符数的长度上限、标量强制、列规格自身的校验与重复列代码；**未知列名消毒**、**行数硬上限**、**超长报文先挡后解码** |
| `MjyStructuredAnswerStoreTest.php` | 单元格粒度存储、替换时删掉消失的行、重复写入幂等、代次隔离同号答卷、清空、按答卷清理、两位数行号的顺序 |
| `MjyQuestionExtensionsTest.php` | 属性注册（含 `xssfilter=false`）、主题随 `.lss` 落地、主题缺失时静默降级、合法作答投影、非法作答标记、重投影不追加、删除答卷清副表、上传会话先开后绑；**一道题配置坏掉不影响其余题的闸门**、**拒绝文案转义** |

### 端到端（真实 HTTP）

`platform/deploy/test/run-question-slice.sh`（`TEST_DB=mysql|pgsql`），
fixture `platform/tests/fixtures/surveys/mjy-question-slice.lss`
（三个题组：数组题 `F`、自增表格 `T`＋主题、上传题 `|`）。
七个场景在两种数据库上**全部通过**，逐项断言见
[题型纵切证据](../p0/question-slice.md)。

| 场景 | MariaDB 10.11 | PostgreSQL 16 |
|---|---|---|
| 发布：三种题型的列与元数据 | 通过 | 通过 |
| 作答：三种题型的真实 HTTP 填写 | 通过 | 通过 |
| 服务端拒收：两条闸门 | 通过 | 通过 |
| 断点续答 | 通过 | 通过 |
| 修改已提交的答卷 | 通过 | 通过 |
| 导出：引擎导出与主副表合并导出 | 通过 | 通过 |
| 主题升级与重新导入 | 通过 | 通过 |

引擎自带 unit 套件保持 848 用例、0 错误 0 失败（1 warning、1 risky 为基线既有）。

## 已知限制与后续

1. **引擎没有「否决单题作答」的事件。** 现行做法是 `beforeSurveyPage` 把非法值清空，
   依赖题目的必答属性把作答者留在本页。**非必答题拦不住**：清空即等于没填，
   引擎会放行。备选：给需要校验的自增表格强制 `mandatory=Y`（已选），
   或登记一条核心补丁，在 `LimeExpressionManager::ProcessCurrentResponses()`
   里加一个可以置 `invalidAnswerString` 的插件事件。
2. **`preg` / `em_validation_q` 看到的是实体编码后的值。** 自由文本题的
   `.NAOK` 取值先过 `htmlSpecialCharsUserValue()`
   （`application/helpers/expressions/em_manager_helper.php:8950`），
   其中 `{`→`&#123;`、`}`→`&#125;`（同文件 `:10307`）。想用正则匹配 JSON，
   模式必须写成 `/^&#123;\x22v\x22:...&#125;$/` 这种形状；`"` 还得写成 `\x22`，
   因为整个模式会被塞进 EM 的双引号字符串里。这条闸门可用但**紧贴引擎内部实现**，
   引擎升级时必须回归。
3. **上传的临时文件名不是资产标识。** 页面提交时引擎把 `futmp_<随机>` 改名成
   一个**全新的** `fu_<随机>`（`application/helpers/expressions/em_manager_helper.php:8809`），
   且这一步**不派发任何事件**。只能用「原始文件名＋大小＋到达顺序」回绑，
   同一题里上传两个同名同大小的文件时绑定顺序不可区分。
   备选：登记核心补丁，在改名处派发事件并把旧名带上。
4. **题型主题的 `answercolumndefinition` 在 7.1.2 里是死代码。**
   `createFieldMap()` 的守卫判断 `isset($arow['attribute'])`
   （`application/helpers/common_helper.php:1748`），而它的主查询
   （同文件 `:1704-1711`，`SELECT g.*, q.*, gls.*, qls.*`）根本没有 `attribute` 列，
   所以永远为假。结果：自增表格的答卷列只能是默认的 `text`（MySQL 65,535 字节）。
   按 20 列 × 50 字符估算，上限约 200 行。备选：平台在列规格里限制行数×列宽（已选），
   或登记核心补丁修正那个守卫。
5. **`update_response` 完全绕过校验。** RemoteControl 的
   `update_response`（`application/helpers/remotecontrol/remotecontrol_handle.php:3535`）
   直接 `SurveyDynamic::encryptSave()`，不跑 EM、不跑必答、不跑插件闸门，
   任何字符串都能写进去。好在它会触发 `afterSurveyDynamicSave`，
   插件能把这条标成 `is_valid=0`。平台侧的答卷编辑入口必须自己校验。
6. **题型主题的 twig 受沙箱限制。** 过滤器/函数/属性白名单在
   `application/config/internal.php:329-400`，例如 `escape` 可用而别名 `e` 不可用。
   主题里任何越界调用都会变成 500，且只在作答时才暴露。
   后续：把题型主题纳入发布前的冒烟渲染。
7. **`answercolumndefinition` 不能用 CDATA 包。** `QuestionTheme::getAnswerColumnDefinition()`
   用 `json_decode(json_encode($xml))[0]` 取值（`application/models/QuestionTheme.php:799`），
   CDATA 会让结果变成空数组。属于引擎的取值方式脆弱，写主题时要注意。
8. **插件属性的 XSS 过滤依赖插件本身在线。** `QuestionAttribute::filterXss()`
   只在属性定义里写了 `xssfilter => false` 时才跳过净化，而定义来自
   `newQuestionAttributes`。插件停用时导入同一份 `.lss`，列定义 JSON 会被
   HTMLPurifier 改写。发布机器人必须保证目标实例的插件已启用。
9. 副表目前只投影自增表格。动态矩阵、级联选择等 C 档题型复用同一张表，
   但 `question_code` 之外还需要一个「结构版本」列，等 WP-02 的题型工厂定下来再加。
10. 副表没有做迁移版本号（与 `MjyPlatformBridge` 同样的问题）：`ensureSchema` 只建表不升级。
11. 大答卷量下的副表写入没有压测。一次保存会按题目删后重插全部单元格，
    且是一格一条 `INSERT`；硬上限（500 行）把最坏情况框住了，但正式版应改成批量插入。
12. 副表的 `errors` 列存的是消毒后的文案，但平台侧任何渲染这一列的界面仍应自行转义。

## 对 WP-02 的影响

引擎自带 29 个题型（`lime_question_themes` 里 `extends=''` 的行）。按上面的三档分：

- **A 档（原生，零扩展）**：`L`（单选）、`!`（下拉）、`5`、`O`、`M`（多选）、`P`、
  `S`/`T`/`U`（文本）、`Q`（多项文本）、`N`/`K`（数值）、`D`（日期）、`G`、`Y`、`I`、
  `R`（排序）、`X`（说明）、`*`（计算）、`|`（上传），以及全部 9 种数组题
  （`F`/`A`/`B`/`C`/`E`/`H`/`1`/`:`/`;`）。平台 DSL 直接映射，发布链路已在 ADR 0005 验证。
- **B 档（加题型主题）**：图片单选/多选、按钮组、滑块、星级、下拉数组等「换皮」题型。
  引擎已自带 7 个示例主题（`themes/question/`），照抄即可，**不需要插件**。
- **C 档（要副表）**：自增表格、动态矩阵、可增删的多附件清单 —— 凡是「行数由作答者决定」
  的形态都在这一档，全部复用本 ADR 的 JSON 信封 ＋ 副表契约。
- **需要核心补丁才能做的**：目前只有两类，
  (a) 非必答题的服务端强制校验（限制 1），
  (b) 附件与平台资产的强绑定（限制 3）。
  两条都有可接受的替代方案，P0 不开核心补丁。
