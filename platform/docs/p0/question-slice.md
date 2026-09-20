# P0-00.3 题型纵切

引擎版本 LimeSurvey 7.1.2。决定与取舍见 [ADR 0006](../adr/0006-question-types.md)，
本文只放实测证据。

运行方式（两种数据库分别跑一遍）：

```bash
platform/deploy/test/run-tests.sh -c platform/phpunit-questions.xml
TEST_DB=pgsql platform/deploy/test/run-tests.sh -c platform/phpunit-questions.xml

platform/deploy/test/run-question-slice.sh
TEST_DB=pgsql platform/deploy/test/run-question-slice.sh
```

## 一、三条纵切是什么

| 纵切 | 题目代码 | 引擎题型 | 扩展 |
|---|---|---|---|
| 原生数组题 | `QMATRIX` | `F`（Array） | 无 |
| 自增表格 | `QTABLE` | `T`（长文本） | 题型主题 `mjy-repeating-table` ＋ 插件 `MjyQuestionExtensions` ＋ 两张副表 |
| 录音／附件 | `QUPLOAD` | `|`（File upload） | 插件的上传会话副表 |

一份 fixture 把三者放在同一问卷的三个题组里：
`platform/tests/fixtures/surveys/mjy-question-slice.lss`（格式 `G`，一组一页，
`allowsave=Y`、`alloweditaftercompletion=Y`，所有问卷设置写显式值，不留 `I`）。

## 二、答卷表长什么样（实测，MariaDB）

激活后 `lime_responses_<sid>`：

```
id int(11)              token varchar(36)         submitdate datetime
lastpage int(11)        startlanguage varchar(20) seed varchar(31)
startdate datetime      datestamp datetime
Q1179_S1182 varchar(5)  ← QMATRIX / SQ001
Q1179_S1183 varchar(5)  ← QMATRIX / SQ002
Q1179_S1184 varchar(5)  ← QMATRIX / SQ003
Q1180 text              ← QTABLE，整张表一块 JSON
Q1181 text              ← QUPLOAD，文件清单 JSON
Q1181_Cfilecount int(11)
```

三条结论：

1. **数组题的每个子题各占一列**，取值是选项代码（`varchar(5)`）。平台不需要任何扩展，
   语义完整，导出也天然分列。
2. **自增表格只占一列 `text`**。题型主题声明的 `answercolumndefinition`（想提升成
   `mediumtext`）**不生效**，见下文引擎缺口 D。
3. **上传题占两列**：文件清单 JSON ＋ 计数列 `_Cfilecount`。

列名一律是 `Q<qid>` 系列，只由数字主键决定
（`application/helpers/common_helper.php:1759`），与 ADR 0005 场景二一致 ——
副表绝不能拿字段名当键。

## 三、生命周期逐步结果

`platform/tests/e2e/question_slice.php`（支撑代码在同目录的 `question_slice_support.php`），
七个场景在 MariaDB 10.11 与 PostgreSQL 16 上
**全部通过**。下表按任务要求的链路重排。

| 步骤 | QMATRIX（原生数组） | QTABLE（自增表格） | QUPLOAD（上传） |
|---|---|---|---|
| 起草定义 | `.lss` 的 `questions`＋`subquestions`＋`answers` | `.lss` ＋ `question_attributes`（`mjy_table_columns` 等）＋ `question_theme_name` | `.lss` ＋ `question_attributes`（`max_num_of_files` 等） |
| 编译 / 导入 | 通过 | 通过；列定义 JSON 未被 XSS 过滤改写 | 通过 |
| 激活 | 通过，3 列 | 通过，1 列（`text`，非 `mediumtext`） | 通过，2 列 |
| 真实 HTTP 作答 | 通过，`A1/A3/A4` 入列 | 通过，服务端归一化后入库 | 通过，经 `/uploader/index?mode=upload` 上传 |
| 断点续答 | 通过，第一页答案保留 | 通过，续答阶段填的内容进副表 | 未在续答场景中测（同页机制，无差异） |
| 改已提交答卷 | 未单独断言（同机制） | 通过，副表跟随更新；写垃圾时标记 `is_valid=0` | 未单独断言（同机制） |
| 引擎导出 | 通过，逐子题列 | 通过，一整块 JSON，**没有逐行列** | 通过，文件清单 JSON，**没有平台资产 id** |
| 主副表合并导出 | 不需要 | 通过，逐行逐列 | 通过，带 `upload_token` |
| 重新导入 / 主题升级 | 通过 | 通过；主题缺失时**静默降级** | 通过 |

### 3.1 作答被服务端归一化

作答者提交的（列顺序打乱、带空白）：

```json
{"v":1,"rows":[{"qty":"2","item":"  录音笔  ","note":"备用"},{"note":"","qty":"1","item":"三脚架"}]}
```

入库的（列顺序按规格、去空白、重新编码）：

```json
{"v":1,"rows":[{"item":"录音笔","qty":"2","note":"备用"},{"item":"三脚架","qty":"1","note":""}]}
```

### 3.2 副表实测行（`lime_mjyquestionextensions_answer_cell`）

```
survey_id  response_id  question_code  row_index  column_code  cell_value
624911     1            QTABLE         0          item         录音笔
624911     1            QTABLE         0          qty          2
624911     1            QTABLE         0          note         备用
624911     1            QTABLE         1          item         三脚架
624911     1            QTABLE         1          qty          1
624911     1            QTABLE         1          note
```

状态表 `lime_mjyquestionextensions_answer_state` 同一自然键，
记 `row_count=2, is_valid=1, errors=''`。

### 3.3 上传会话实测行（`lime_mjyquestionextensions_upload_session`）

```
upload_token   6e54ab1d-71f5-4805-b9c6-e73cbba1f755   ← 平台侧资产 id
response_id    1
question_code  QUPLOAD
original_name  recording.txt
temp_name      futmp_xcgv89czntnp7bd_txt   ← beforeProcessFileUpload 时看到的名字
stored_name    fu_hzggmdctu5zfwtm          ← 提交后引擎重新随机的名字
size_bytes     34
state          bound
```

`temp_name` 与 `stored_name` 毫无关系，中间没有任何事件 —— 这是引擎缺口 C。

### 3.4 引擎导出（`export_responses`，csv/code/short）

表头：

```
"id";"submitdate";"lastpage";"startlanguage";"seed";"startdate";"datestamp";
"QMATRIX[SQ001]";"QMATRIX[SQ002]";"QMATRIX[SQ003]";"QTABLE";"QUPLOAD";"QUPLOAD[filecount]"
```

数据行里 `QTABLE` 是转义后的整块 JSON，`QUPLOAD` 是文件清单 JSON：

```
"A1";"A3";"A4";
"{""v"":1,""rows"":[{""item"":""录音笔"",""qty"":""2"",""note"":""备用""},…]}";
"[{ ""title"":"""",""comment"":"""",""size"":0.033203125,""name"":""recording.txt"",
   ""filename"":""fu_hzggmdctu5zfwtm"",""ext"":""txt"" }]";"1"
```

**结论**：数组题的导出可以直接给业务用；自增表格与上传题的导出**只能给平台用**，
必须由平台把副表并进去才有逐行/逐资产的形状。`get_uploaded_files` 也只给引擎文件名，
不带平台资产 id（实测断言）。

### 3.5 插件测试

`plugins/MjyQuestionExtensions/tests/`：35 个用例，两种数据库均通过。
其中 5 个用例专门盯安全边界：未知列名消毒（错误文案会被模板 `raw` 渲染）、
行数硬上限、超长报文先挡后解码、一道题配置坏掉不影响其余题的闸门、拒绝文案转义。

## 四、引擎缺口清单（本次新发现）

| 编号 | 缺口 | 代码位置 | 影响 | 最便宜的替代 |
|---|---|---|---|---|
| A | **没有插件事件能否决单道题的作答并把人留在本页** | `afterResponseSave` 在写库之后；`SurveyIndex.php:228` 的 `beforeSurveyPage` 是唯一早于 EM 的落脚点（在那里 `renderExitMessage()` 只能整体终止请求，见 00.7 发现 12） | 扩展题型的服务端校验只能靠改写 `$_POST` ＋ 引擎自己的必答校验 | 需要校验的题强制 `mandatory=Y`（已选）；否则要核心补丁 |
| B | **EM 看到的自由文本值是实体编码后的** | `em_manager_helper.php:8950` → `:10307`（`{`→`&#123;`、`}`→`&#125;`） | `preg` / `em_validation_q` 无法用自然写法匹配 JSON | 模式按编码后形状写（已选，见 fixture 注释）；引擎升级时必须回归 |
| C | **上传文件改名不派发事件** | `em_manager_helper.php:8809`（`futmp_*` → 全新的 `fu_*`） | 临时文件名不能当资产标识 | 按「原始文件名＋大小＋到达顺序」回绑（已选）；同名同大小时不可区分 |
| D | **题型主题的 `answercolumndefinition` 是死代码** | `common_helper.php:1748` 的守卫判断 `$arow['attribute']`，而主查询（`:1705`）没有该列 | 自增表格只能落在 `text`（65,535 字节），约 200 行上限 | 平台在列规格里限制行数×列宽（已选） |
| E | **题型主题缺失时导入静默降级** | `Question.php:1531-1556` `questionThemeNameValidator()` | `.lss` 在没装主题的实例上会变成普通长文本，不报错不告警 | 发布网关回读 `question_theme_name` 校验（已选） |
| F | **`update_response` 绕过全部校验** | `remotecontrol_handle.php:3535` 直接 `encryptSave()` | 已提交答卷可以被改成任意字符串 | 触发 `afterSurveyDynamicSave`，插件标 `is_valid=0`（已选）；平台编辑入口自己校验 |
| G | **题型主题 twig 受沙箱白名单限制** | `application/config/internal.php:329-400` | 越界调用（例如过滤器别名 `e`）在作答时变成 500 | 把题型主题纳入发布前冒烟渲染 |
| H | **`answercolumndefinition` 不能用 CDATA** | `QuestionTheme.php:799` 的 `json_decode(json_encode($xml))[0]` | 写主题时容易踩空 | 写主题时不用 CDATA |
| I | **插件属性的 XSS 过滤依赖插件在线** | `QuestionAttribute::filterXss()` 取定义自 `newQuestionAttributes` | 插件停用时导入，列定义 JSON 会被 HTMLPurifier 改写 | 发布前校验目标实例插件已启用 |
| J | **上传端点也要 CSRF 令牌** | `UploaderController`（经 Yii CSRF 过滤） | 自动化/代填链路必须先取问卷页的 `YII_CSRF_TOKEN` | 已在 e2e 里处理 |

## 五、副表契约（最终形态）

```sql
-- 一个单元格一行
{prefix}mjyquestionextensions_answer_cell (
  id                 pk,
  engine_instance_id string(64)  NOT NULL,
  survey_id          integer     NOT NULL,
  generation         string(36)  NOT NULL,   -- 取自 mjyplatformbridge_generation
  response_id        integer     NOT NULL,
  question_code      string(64)  NOT NULL,   -- lime_questions.title，不是字段名
  row_index          integer     NOT NULL,   -- 从 0 开始
  column_code        string(64)  NOT NULL,   -- mjy_table_columns 里的 code
  cell_value         text        NULL,
  updated_at         datetime    NOT NULL,
  UNIQUE (engine_instance_id, survey_id, generation, response_id,
          question_code, row_index, column_code)
);

-- 一次作答一行
{prefix}mjyquestionextensions_answer_state (
  id                 pk,
  engine_instance_id string(64)  NOT NULL,
  survey_id          integer     NOT NULL,
  generation         string(36)  NOT NULL,
  response_id        integer     NOT NULL,
  question_code      string(64)  NOT NULL,
  row_count          integer     NOT NULL,
  is_valid           integer     NOT NULL,   -- 0 = 引擎里存着原始文本但不可信
  errors             text        NULL,       -- 最多 1000 字
  updated_at         datetime    NOT NULL,
  UNIQUE (engine_instance_id, survey_id, generation, response_id, question_code)
);

-- 上传会话
{prefix}mjyquestionextensions_upload_session (
  id                 pk,
  upload_token       string(36)  NOT NULL UNIQUE,  -- 平台侧资产 id
  engine_instance_id string(64)  NOT NULL,
  survey_id          integer     NOT NULL,
  generation         string(36)  NOT NULL,
  response_id        integer     NOT NULL,         -- 0 = 上传时答卷行还不存在
  question_code      string(64)  NOT NULL,
  field_name         string(128) NULL,             -- 本次激活期内的 Q<qid>
  original_name      string(255) NULL,
  temp_name          string(255) NULL,             -- futmp_*
  stored_name        string(255) NULL,             -- fu_*，绑定后才有
  extension          string(16)  NULL,
  size_bytes         integer     NOT NULL,
  state              string(16)  NOT NULL,         -- received | bound
  created_at         datetime    NOT NULL,
  bound_at           datetime    NULL,
  INDEX (engine_instance_id, survey_id, generation, response_id, question_code)
);
```

写入规则：

- 投影入口只有一个：`MjyQuestionExtensions::projectResponse()`，
  由 `afterResponseSave` / `afterSurveyDynamicSave` 触发，也可由平台补投。
- 一次投影 = 先按 `(实例, 问卷, 代次, 答卷, 题目代码)` 删掉旧单元格再插新的（同一事务），
  所以重复投影幂等，行数变少时不会留残行。
- 校验不通过时单元格表清空、状态表记 `is_valid=0` ＋ 原因。
- `afterResponseDelete` / `afterSurveyDynamicDelete` 按答卷清理两张表与上传会话。
- 所有钩子体裹在 `safely()` 里，异常只写日志，绝不打断作答者或管理员的请求
  （与 `MjyPlatformBridge` 一致）。

## 六、对 WP-02 的 47 个题型的判定

引擎自带 29 个基础题型（`lime_question_themes` 中 `extends=''`）。
把平台目录按「作答数据的形状」归档，得到四类：

### A 档 — 原生，零扩展（覆盖平台目录的绝大多数）

单选 `L` / 下拉 `!` / 五点 `5` / 单选带备注 `O`；多选 `M` / 多选带备注 `P`；
文本 `S`/`T`/`U` / 多项文本 `Q`；数值 `N` / 多项数值 `K`；日期 `D`；性别 `G`；
是否 `Y`；语言 `I`；排序 `R`；说明 `X`；计算 `*`；上传 `|`；
数组九件套 `F`/`A`/`B`/`C`/`E`/`H`/`1`/`:`/`;`。

判定依据：本次实测的 `QMATRIX` 走完整条链路没有任何额外动作，
且 ADR 0005 已验证这些类型的 `.lss` 发布与字段映射。

### B 档 — 需要题型主题，不需要插件

图片单选／多选、按钮组、滑块、星级、下拉式数组、拖拽排序等「换皮」题型：
数据形状与基础题型完全相同，只换 twig 与 JS。引擎已自带 7 个示例主题
（`themes/question/`）可以照抄。风险只有两条：主题必须随镜像预装（缺口 E），
twig 受沙箱限制（缺口 G）。

### C 档 — 需要副表

**自增表格**、动态矩阵（行数由作答者决定）、可增删的多附件清单、
级联/联动选择（选项集在作答时才确定）。共同特征：**行数或列集合不是发布期常量**，
引擎的列式答卷表表达不了。全部复用本文的 JSON 信封 ＋ 副表契约，
每多一种只多一份列规格与一个题型主题，不需要新的存储设计。

### D 档 — 没有核心补丁做不了（本次只找到两条）

1. **非必答的强制服务端校验**（缺口 A）。现在的替代是把这类题强制设为必答；
   如果业务要求「可以不填，但填了就必须合法」，需要核心补丁。
2. **附件与平台资产的强绑定**（缺口 C）。现在的替代是按文件名＋大小＋顺序回绑；
   如果业务要求「同一题可以上传多个同名同大小的文件并各自可追溯」，需要核心补丁。

其余的「做不了」都不是题型问题，而是运行期策略问题（计时、防作弊、配额），
属于 P0-00.7 的范围。

### 工作量判断

- A 档：只做 DSL → `.lss` 的映射，按题型计约 0.5 人日/型。
- B 档：一个主题（config.xml ＋ twig ＋ 可选 JS/CSS）约 1 人日/型，
  加上一次沙箱冒烟渲染。
- C 档：第一种（自增表格）已经把副表、校验器、投影、导出合并都做完；
  后续每一种约 2 人日（列规格 ＋ 主题 ＋ 测试），**不再需要新的架构决策**。

## 七、未覆盖的部分

- 加密题目（`encrypted=Y`）与自增表格的组合没有测；引擎会把列改成 `text` 并加密，
  副表投影读到的是解密后的值还是密文尚未确认。
- 多语言问卷里列定义是否需要按语言分开（`mjy_table_columns` 目前不分语言）。
- 大答卷量：一次保存会删后重插全部单元格，20 列 × 200 行的单次写入没有压测。
- 副表迁移版本号：`ensureSchema` 只建表不升级（与 `MjyPlatformBridge` 同样的问题）。
- 断点续答场景里上传题没有单独断言（与普通页面保存同一机制）。
