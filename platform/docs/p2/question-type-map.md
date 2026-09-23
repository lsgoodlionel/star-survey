# WP-02 题型映射表（切片 02.1–02.3）

引擎 LimeSurvey 7.1.2。承载方式三档见 [ADR 0006](../adr/0006-question-types.md)（A 原生、B 原生＋题型主题、C 副表＋插件）；
本文把需求矩阵 WP-02 的 47 条（R02-01…R02-47）逐条落到五类之一，并给出答案代码、缺失值与导出列映射。
「本批」一列标 ✔ 的在本切片实现（发布网关 DSL、校验、编译、绑定与指纹、字段字典、真引擎 e2e）。

## 一、五类

| 类 | 含义 | 条数 |
|---|---|---|
| **N** 原生 | 引擎自带题型＋题目属性，平台只做 DSL 映射与服务端规则编译 | 10 |
| **T** 原生＋主题 | 数据形状与原生一致，只换展示（引擎自带主题或我方新主题，`themes/question/mjy-*`） | 6 |
| **P** 插件扩展 | 数据形状引擎表达不了：JSON 信封＋插件校验＋副表（`MjyQuestionExtensions`） | 16 |
| **L** 平台侧／后续切片 | 依赖平台服务（字典、容量、价格、计时、关联查询）或逻辑 DSL 的后续切片 | 8 |
| **B** 阻塞 | 依赖外部供应商或授权渠道，未签约前不做、不伪造 | 7 |

切片 02.1–02.2（✔）共 **11** 条整条交付（N 10 条＋T 中的 R02-10），另有 3 条交付其中的原生子集（R02-07、R02-43、R02-31 的校验码）。

切片 02.3（✔2）再交付 **6** 条：需要新题型主题的 R02-04（单选）、R02-07（内嵌填空）、R02-14、R02-28、R02-43，
以及走副表的 R02-13（自增表格的 DSL 映射）与 R02-19（选点）。副表的「结构版本」列先行落地
（[副表契约 v1](../../contracts/question-extension-tables-v1.md)），其余 14 条 P 类由此解除阻塞。

## 二、逐条映射

列说明：**引擎承载**＝题型字母＋关键属性；**答案代码**＝答卷列里存的值；**缺失值**见第四节的三种状态；
**导出列**＝绑定映射里的 (aid, 尺度) 形状（平台字段字典按它出标签，引擎导出表头按 `代码[aid]`）。

| 需求 | 名称 | 类 | 本批 | 引擎承载 | 答案代码 | 导出列 (aid) |
|---|---|---|---|---|---|---|
| R02-01 | 单选 | N | ✔ | `L` 单选按钮、`!` 下拉；`other` 开「其他」 | 选项代码（≤5 位字母数字）；其他＝`-oth-` | `""`；其他文本 `other` |
| R02-02 | 多选 | N | ✔ | `M`／`P`（带评论）；`min_answers`／`max_answers`；子题 `exclusive:true`→`exclude_all_others`＋服务端互斥规则 | 每个选项一列，选中＝`Y`，未选＝`""` | 子题代码；`P` 另有 `代码comment`；其他 `other`（`P` 再加 `othercomment`） |
| R02-03 | 多级下拉 | P | | 字典版本＝副表的结构版本（已落地）；级联清值与字典服务仍缺，放 02.4 | — | 后续 |
| R02-04 | 选项分类 | T | ✔2（单选） | `L`＋新主题 `mjy-grouped-options`：分组必须**恰好覆盖**全部选项，否则 422 | 同 R02-01 | `""` |
| R02-05 | 漏斗选项 | L | | 原生 `array_filter`，但它是表达式属性，v2 禁止直写；归逻辑 DSL 的 `filter` 切片（WP-03） | — | 后续 |
| R02-06 | 填空与多项填空 | N | ✔ | `S` 短文本、`Q` 多项填空、`K` 多项数值；`maxLength`、`format`（见第三节）编译成服务端校验 | 原文 | `""`；`Q`/`K` 子题代码 |
| R02-07 | 填空选择组合 | T | ✔2 | 原生子集：`L`＋其他、`O` 单选带评论；内嵌填空＝`P`＋新主题 `mjy-inline-blank`，清值用原生 `commented_checkbox=checked`，长度上限编译成服务端规则 | 选中＝`Y`；填空＝原文 | 子题代码＋`代码comment` |
| R02-08 | 超长文本 | N | ✔ | `T`／`U`；`maxLength`（≤16000 字）编译成按字符计数的服务端规则，超限留在本页并提示 | 原文 | `""` |
| R02-09 | 评分与量表 | N | ✔ | `5` 五分制、`A` 五分矩阵、`B` 十分矩阵、`F`＋自定义刻度；正反向用选项 `assessmentValue` | `5`/`A`：`1`–`5`；`B`：`1`–`10`；`F`：选项代码 | `""`；矩阵为子题代码 |
| R02-10 | NPS 与评价组件 | T | ✔ | NPS＝`L`＋选项 `0`…`10`＋引擎自带主题 `bootstrap_buttons`；星级＝`5`＋`slider_rating=1` | `0`–`10`；`1`–`5` | `""` |
| R02-11 | 循环评价 | P | | 评价对象动态、要稳定对象 ID；引擎无循环 | — | 后续 |
| R02-12 | 矩阵与表格 | N | ✔ | `F`、`H`（按列）、`A`/`B`/`C`/`E`（固定刻度）、`1` 双尺度、`:` 数值矩阵、`;` 文本矩阵 | 选项代码；`C`：`Y`/`U`/`N`；`E`：`I`/`S`/`D`；`:`/`;` 原值 | 子题代码；`1` 为子题代码＋尺度 0/1；`:`/`;` 为 `行_列` |
| R02-13 | 自增表格 | P | ✔2 | `T`＋`mjy-repeating-table`＋副表；列定义、行数上下限与结构版本走 `themeOptions` | JSON 信封 `{"v":1,"rows":[…]}` | `""`；单元格在副表，按结构版本解释 |
| R02-14 | 矩阵单题作答 | T | ✔2 | `F`＋新主题 `mjy-matrix-stepper`（只接管非下拉布局）；隐藏的行照样提交，必答在服务端重算 | 同 R02-12 | 子题代码 |
| R02-15 | 排序 | N | ✔ | `R`（7.x 起排序项是**子题**）；`max_subquestions` 限制名次数 | 引擎存一列 JSON 数组（按名次排列的子题代码） | `""`＝JSON 列；名次 `1`…`n` 是 fieldmap 里的虚列（无物理列） |
| R02-16 | 比重分配 | N | ✔ | `K`＋`equals_num_value`（和为定值）＋`min_num_value_n=0`＋`num_value_int_only`；滑块＝`slider_layout=1` | 数值 | 子题代码 |
| R02-17 | 图片 PK | P | | 配对随机要可追溯 | — | 后续 |
| R02-18 | 货架题 | P | | 商品/坐标与货架版本 | — | 后续 |
| R02-19 | 热力图与选区 | P | ✔2（选点） | `T`＋新主题 `mjy-heatmap`＋副表；坐标归一化到 `[0,1]`，由插件逐格校验 | JSON 信封，两列 `x`/`y` | `""`；坐标在副表 |
| R02-20 | 轮播图 | P | | 媒体版本留存依赖资产服务 | — | 后续 |
| R02-21 | 视频题 | P | | 授权播放依赖资产服务 | — | 后续 |
| R02-22 | 文字点睛 | P | | 原文版本＋偏移 | — | 后续 |
| R02-23 | 文件上传 | N | ✔（基础） | `|`；`allowed_filetypes` 必填（拒绝可执行／可渲染扩展名）、`max_filesize`、`max_num_of_files`／`min_num_of_files` | 文件清单 JSON；计数 | `""`＋`filecount`；病毒扫描、私有下载、平台资产 id 见 ADR 0006 |
| R02-24 | 语音录入 | B | | 需 ASR 供应商 | — | — |
| R02-25 | 答题录音 | P | | 录音主题＋上传会话（P0 已有会话表） | — | 后续 |
| R02-26 | 答题摄像 | P | | 视频上传完整性与留存 | — | 后续 |
| R02-27 | 手机验证 | B | | 验证码需短信供应商；**号码格式**校验在本批（`format: cn_mobile`） | — | — |
| R02-28 | 扫码录入 | T | ✔2 | `S`＋新主题 `mjy-scan-input`（BarcodeDetector）；**必须设 `maxLength`**，扫到的内容整串由客户端提交 | 原文 | `""` |
| R02-29 | OCR | B | | 需 OCR 供应商 | — | — |
| R02-30 | 学信网验证 | B | | 无合法接口时明确阻塞 | — | — |
| R02-31 | 企业信息查询 | B | 子集 | 企业数据需授权数据源；**统一社会信用代码校验码**在本批（`format: cn_uscc`） | — | — |
| R02-32 | 双码拼接 | P | | 图片合成归档 | — | 后续 |
| R02-33 | 地图 | B | | 地图 API 授权与坐标系 | — | — |
| R02-34 | 门店选择 | L | | 授权门店字典 | — | 后续 |
| R02-35 | 城市级别 | L | | 城市分级字典 | — | 后续 |
| R02-36 | 日期范围 | N | ✔ | `D`＋`date_min`／`date_max`（字面日期 `YYYY-MM-DD`）＋`date_format` | `YYYY-MM-DD HH:MM:SS` | `""` |
| R02-37 | 预约题 | L | | 时段容量服务 | — | 后续 |
| R02-38 | 商品题 | L | | SKU/价格由服务端给 | — | 后续 |
| R02-39 | 绘制签名 | P | | 画布资产 | — | 后续 |
| R02-40 | 在线签署 | B | | 电子签供应商 | — | — |
| R02-41 | 考试绘图与文件题 | P | | 答案资产快照（WP-09） | — | 后续 |
| R02-42 | 分页计时器 | L | | 引擎计时器只在前端，服务端截止时间归运行时策略 | — | 后续 |
| R02-43 | 折叠栏目 | T | ✔2 | `X`＋新主题 `mjy-collapsible`：说明题作分段标题，把随后的题目折进 `<details>` | `X` 不存值 | `""`（引擎照样建一列） |
| R02-44 | Vlookup 问卷关联 | L | | 平台关联查询 | — | 后续 |
| R02-45 | CATI 组件 | L | | 访员工作台 | — | 后续 |
| R02-46 | 心理实验组件 | P | | 试次/反应时副表 | — | 后续 |
| R02-47 | 专业模型组件 | P | | 按模型生成结构 | — | 后续 |

## 三、DSL 扩展（兼容 definitionVersion 1 与 2）

不新增 definitionVersion：新题型字母与下列可选键在 v1、v2 都可用；**不带这些键的旧定义编译结果逐字节不变**
（`test_logic_compile` 与 `test_question_types` 里的 SHA-256 金标准）。

| 键 | 位置 | 适用题型 | 编译到 |
|---|---|---|---|
| `format` | 题目 | `S` | `em_validation_q`（未作答放行）＋默认提示；与 v2 `validation` 用 `and` 合并 |
| `maxLength` | 题目 | `S` `T` `U` | 属性 `maximum_chars`＋服务端规则 `strlen(html_entity_decode(X.NAOK)) <= n` |
| `exclusive` | 子题 | `M` `P` | 属性 `exclude_all_others`（分号连接）＋服务端规则：互斥项选中时其余各列必须为空 |

`format` 取值：

| 值 | 规则（服务端 ExpressionScript） |
|---|---|
| `email` | `regexMatch` 形状校验 |
| `cn_mobile` | `^1[3-9]` 开头的 11 位数字 |
| `cn_postcode` | 6 位数字 |
| `cn_id_card` | 18 位；出生日期 `checkdate()`；GB 11643 加权模 11 校验码（末位 `X` 大小写均可） |
| `cn_uscc` | 18 位，字符集 `0-9A-HJ-NPQRTUWXY`；GB 32100 加权模 31 校验码 |

规则里的正则一律不含 `{` `}` `\`（引擎把答案先做实体编码、把模式塞进双引号字符串，见 ADR 0006 限制 2），
重复次数展开成字符组；正则作用在 `html_entity_decode(X.NAOK)` 上（原文而非实体编码后的值）。原生 `maximum_chars` 仍原样透传（只在浏览器端生效，旧定义行为不变）。

**为什么是 `em_validation_q` 而不是 `preg`**：两者都在服务端 `_ValidateQuestion` 里重算（相关性也在服务端重算，
不信任表单里的 `relevance<qid>`），但 `preg` 只能做正则，身份证与信用代码的校验码必须用表达式。

## 三之二、题型主题与 `themeOptions`（切片 02.3）

仍然不新增 definitionVersion。题目多一个可选键 `themeOptions`（对象），只有用平台自带主题
（`mjy-` 前缀）的题目才允许带它；**不带这个键的旧定义编译结果逐字节不变**。

| 主题 | 需求 | 题型 | `themeOptions` | 编译到 |
|---|---|---|---|---|
| `mjy-collapsible` | R02-43 | `X` | `summary`（必填）、`collapsed` | `mjy_collapse_summary`、`mjy_collapse_default` |
| `mjy-scan-input` | R02-28 | `S` | `scanFormat`（qr/barcode/any）、`manualEntry` | `mjy_scan_format`、`mjy_scan_manual`；**题目必须另设 `maxLength`** |
| `mjy-grouped-options` | R02-04 | `L` | `groups`（必填）、`collapsible` | `mjy_option_groups`（JSON）、`mjy_option_groups_collapsible` |
| `mjy-matrix-stepper` | R02-14 | `F` | `rowsPerStep`、`showProgress` | `mjy_stepper_rows`、`mjy_stepper_progress` |
| `mjy-inline-blank` | R02-07 | `P` | `blankLabel`、`blankMaxLength`（必填） | `commented_checkbox=checked`＋每处填空一条长度规则 |
| `mjy-repeating-table` | R02-13 | `T` | `structureVersion`、`columns`（均必填）、`minRows`、`maxRows` | `mjy_table_columns`、`mjy_table_min_rows`、`mjy_table_max_rows`、`mjy_structure_version` |
| `mjy-heatmap` | R02-19 | `T` | `structureVersion`、`image`（均必填）、`minPoints`、`maxPoints` | 同上（列定义由平台生成：`x`/`y` decimal，范围 `[0,1]`） |

422 错误码：`E_THEME_UNKNOWN`（`mjy-` 前缀却没注册＝拼错，目标实例上会静默降级）、
`E_THEME_TYPE_MISMATCH`、`E_THEME_OPTIONS_UNSUPPORTED`、`E_THEME_OPTION_UNKNOWN`、
`E_THEME_OPTION_REQUIRED`、`E_THEME_OPTION_VALUE`、`E_THEME_ATTRIBUTE_MANAGED`
（主题生成的属性不许在定义里直写）。引擎自带主题名照旧透传，由发布后的回读核对。

**服务端把关落在哪里**（浏览器端一概不算数）：

| 主题 | 客户端能改什么 | 谁拦住它 |
|---|---|---|
| `mjy-grouped-options` | 提交任意选项代码 | 引擎 `checkValidityAnswer` |
| `mjy-scan-input` | 提交任意字符串 | 编译出的 `em_validation_q` 长度规则（所以 `maxLength` 必填） |
| `mjy-inline-blank` | 没勾选却填了填空、填空超长 | 原生 `commented_checkbox=checked`＋编译出的长度规则 |
| `mjy-matrix-stepper` | 跳过没走到的行 | 引擎必答在服务端重算（隐藏的行照样提交） |
| `mjy-repeating-table`／`mjy-heatmap` | 整块 JSON 信封 | 插件 `beforeSurveyPage` 逐格重算并归一化；不合法时清空＋题目必答把人留在本页（ADR 0006 限制 1，所以这两类题必须设为必答） |

## 三之三、副表的存储契约（给读端）

自增表格与热力图的作答在引擎答卷表里只有**一列 JSON**；真正的结构在
`{prefix}mjyquestionextensions_answer_cell` / `_answer_state`，键是
(引擎实例, 问卷, 代次, 答卷 id, 题目代码)，列与语义见
[副表契约 v1](../../contracts/question-extension-tables-v1.md)。

本切片补上的关键一列是 **`structure_version`**：答案按哪一版列定义写入就记哪一版，
换了列定义之后早先的答卷仍然能按它自己那一版读回来。三条约定：

1. 版本由平台在发布时声明（`themeOptions.structureVersion` → 题目属性 `mjy_structure_version`），
   绑定记录里同时带 `structureVersion` 与由列定义算出的 `structureDigest`；
2. 插件写库前消毒，不合法或缺失一律记 `'0'` ＝「不知道是哪一版」，**不是第 0 版**；
3. `ensureSchema()` 会给契约之前建的表加列并回填 `'0'`，幂等。

**排序题（R02-15）的读回契约。** 引擎只有主列，名次列是 fieldmap 里的虚列（第四节）。
读端（`pubgw/responses.py` 的 `export_responses`）对名次列一定拿到 `null`，这不是缺陷也不是漏答：

```
主列（aid = ""）= JSON 数组，按名次排列的子题代码，例如 ["I3","I1","I2"]
第 n 名          = 主列 JSON 的第 n 个元素（下标 n-1）；数组长度不足即「没排到这一名」
未作答           = 主列为字符串 "[]"（不是 NULL，不是空串）
```

导出与答案读取都应当**从主列解析名次**，不要去读名次列；字段字典把主列与名次列都标上
排序项选项（`QuestionTypeColumns` 的 `R` 分支），供读端做标签。本切片不动 `responses.py`。

## 四、缺失值（实测，MariaDB 10.11 与 PostgreSQL 16 完全一致）

引擎对同一列有三种「没有值」，平台字段字典与导出按下表解释。「显示了、没作答」一行来自
`run-question-types.sh` 场景 B（只答必答题后提交）逐列读库：

| 状态 | 答卷列 | 含义 |
|---|---|---|
| 显示了、没作答 | 文本、选择、矩阵（含 `:` 数值矩阵，它是 text 列）、多选未勾选、`X`：`""`；`N` `K`（decimal 列）与 `D`：`NULL`；排序：`"[]"`；上传：清单 `""`、计数 `0` | 作答者跳过 |
| 被条件隐藏、或被互斥项排除的子题 | `NULL`（`deletenonvalues=1`，引擎缺省） | 不适用 |
| 未到达该页／答卷未提交 | `NULL`，且 `submitdate IS NULL` | 未完成 |

多选勾选为 `Y`；排序的 JSON 数组只含已排的项。

**排序名次列是虚列。** 7.x 的排序题在答卷表里只有主列（JSON），fieldmap 里的名次列 `1…n`
没有物理列：`export_responses` 读名次列恒为 `null`，名次必须从主列 JSON 解析。
**网关读端点替两边解析**（`pubgw/ranking.py`）：它按 `get_fieldmap` 认出排序题（名次列列名
`Q<qid>_S<sqid>` 与多选、数组的子题列同形，光看列名分不出来），把主列 JSON 摊到名次列上——
第 *n* 位是排在该位的项代码，没排到的位置是空串，整题不适用（未到达、被隐藏）时名次列仍为 `null`。
答卷查询接口与导出作业都只经这一个入口拿作答值，因此一次就都对了。
列结构按 `(实例, sid)` 缓存（已发布问卷改版是新 sid，结构不会变）。
绑定映射与指纹照实包含名次列（引擎的 `get_fieldmap` 就是这样），字段字典把主列与名次列都标上排序项选项。

## 五、服务端校验与篡改（「篡改影子字段不能跳过校验」）

`run-question-types.sh`（`TEST_DB=mysql|pgsql`）的场景 C，每条一个新会话、真实 HTTP 表单提交，
两种数据库各 30 条全部通过：

| 类别 | 篡改 | 结果 |
|---|---|---|
| 选项代码 | 单选 `A9`、是否 `X`、NPS `11`、五分 `6`、性别 `Z`、矩阵 `A9`、五分矩阵 `7`、`C` 矩阵 `Z`、双尺度串尺度、排序未知项 | 留在本页（`checkValidityAnswer`） |
| 数量与范围 | 多选超 `max_answers`、数值超上限、整数题填小数、比重和 ≠ 100、负数分配、日期早于 `date_min`、不存在的日期、数值矩阵填非数字、排序同一项排两次 | 留在本页 |
| 中国本地化格式 | 手机号第二位 2、身份证校验码错（同时把 `java<字段>` 写成合法号码）、出生日期不存在、信用代码校验码错、邮编 5 位、邮箱无点、邮箱超 v2 规则长度、昵称超 `maxLength` | 留在本页（`em_validation_q` 服务端重算） |
| 影子字段 | 必答手机号填非法值并把 `relevance<qid>` 写 0（假装被隐藏） | 留在本页：相关性在服务端重算，必答照查 |
| 影子字段 | 互斥项＋其他项，并把所有 `relevance*` 写 1 | 放行但**入库前纠正**：服务端重算子题相关性，被互斥的选项置 `NULL` |
| 越级提交 | 第 1 页直接 `movesubmit` | 不收卷（后页必答题未答） |

未覆盖：上传题伪造文件清单 JSON（引擎只在临时目录找到同名文件时才搬移，找不到时清单原样入库），
平台不得信任清单里的文件名，文件与资产的绑定以 `MjyQuestionExtensions` 上传会话为准（ADR 0006 限制 3）。

## 五之二、切片 02.3 的实测结果

`run-question-themes.sh`（`TEST_DB=mysql|pgsql`，真实 HTTP，两种数据库**各 94 条断言全部通过**）：

| 场景 | 覆盖 | 结果 |
|---|---|---|
| A 冒烟渲染 | 每个新主题的标记与它的 JS 是否真的出现在作答页上（14 个标记） | 通过；没有 twig 沙箱 500 |
| B 完整作答 | 逐列断言（含 `&`、`<` 与取上限的值），副表逐格断言，结构版本 `rt1`／`hm1` 与绑定记录一致 | 通过 |
| C 只答必答题 | 其余列在库里是 `""` 还是 `NULL` | 通过，见下 |
| D 篡改 | 17 条：越界选项代码、扫码超长、没勾选却填填空、填空超长、矩阵漏行、表格超行数／越界数值／非整数／未知列／必填为空／错信封版本／不是 JSON、热力图坐标越界／负数／超点数／非数字，以及把 `relevance*` 影子字段全写 1 | 全部留在本页，且没有任何已提交答卷带着这些值 |
| E 说明题列 | 往 `X` 题的隐藏 input 里塞文字 | 收卷成功，但那一列入库为 `NULL`——注入的文字没有落地 |
| F 读回 | 经网关读端点逐列读回 | 与库里一致 |

本切片题型的缺失值（实测，两种数据库一致）：`QSCAN`（`S`）、`QSEC`（`X`）、
内嵌填空的选项列与填空列都是 `""`；副表题型是必答题，没有「显示了、没作答」这一状态。
**说明文字题被塞值后是 `NULL` 而不是 `""`**，与第四节「显示了、没作答」的 `""` 不同——
读端不要把 `X` 列的 `NULL` 当成「未到达该页」。

单元测试：网关 `python3 -m unittest discover -s tests -t .` 580 条（切片 02.2 时是 535），
插件 PHPUnit 45 条（原 35 条）在 MariaDB 10.11 与 PostgreSQL 16 上全通过，
Java 744 条（含字段字典的主题映射 3 条）。

## 六、本批实现与延后

本批：`L ! M P O 5 Y G F H A B C E 1 : ; S T U Q N K D R | X`（`*` 计算值沿用 WP-03），
以及引擎自带题型主题 `bootstrap_buttons`、`bootstrap_buttons_multi`、`bootstrap_dropdown`、`image_select-listradio`、
`image_select-multiplechoice`、`ranking_advanced`（发布后回读 `question_theme_name` 防静默降级，ADR 0006 决定 6；e2e 实际发布并作答了 `bootstrap_buttons` 与 `image_select-listradio`，其余同类主题未单独跑）。
不收：`I`（语言切换，非采集题型）。

切片 02.3 另交付平台自带题型主题 `mjy-collapsible`、`mjy-scan-input`、`mjy-grouped-options`、
`mjy-matrix-stepper`、`mjy-inline-blank`、`mjy-heatmap`（`mjy-repeating-table` 沿用 P0 的那一个），
都是引擎真的装得上、e2e 真的渲染过的目录。

延后原因：

- **需要新题型主题**：R02-04 的**多选**分组仍未做——多选的选项行由 `rows/*.twig` 包含进来，
  换主题要连行模板一起接管（02.4）。R02-07 的「选项内嵌填空」已交付，`Q` 多项填空型组合仍在原生子集。
- **插件扩展**（P 类余下 14 条）：副表的「结构版本」列已落地（ADR 0006 限制 9 解除），
  余下各条缺的是各自的编辑器主题与列定义生成器，不再缺公共设施。
  R02-03 另需字典服务与级联清值策略；R02-17/20/21/25/26/32/39/41 需资产服务。
- **平台侧**（L 类 8 条）：依赖字典／容量／价格／计时／关联服务，按对应 WP 排期；R02-05 归逻辑 DSL。
- **阻塞**（B 类 7 条）：供应商或授权渠道未落实（`05-供应商询证清单.md`），不做占位实现。
