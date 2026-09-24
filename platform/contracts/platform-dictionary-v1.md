# 契约：平台层级字典（v1）

依据 [ADR 0019](../docs/adr/0019-platform-dictionary.md)。本文写的是**四段接缝**上两端各自约定了什么：

```
运营 ──HTTP──> 平台        /v1/platform/dictionaries/**          建字典、灌节点、发布
租户 ──HTTP──> 平台        /v1/dictionaries/**                   只读已发布的版本
平台 ──定义──> 发布网关     definition.dictionaries[] ＋ themeOptions
网关 ──.lss──> 引擎插件     plugin_settings(MjyQuestionExtensions, dictionaries)
作答者 ─HTTP─> 引擎插件     plugins/direct?function=dictionaryNodes
```

状态：v1（WP-02 字典车道引入）。**改动须同步两端并升版本**：节点三元组的形状、摘要算法、
`plugin_settings` 的键与信封版本都是两端约定。

## 一、字典、版本、节点

一本**字典**是一棵树。一**版**是这棵树在某一时刻的完整快照，发布之后不可变。
一个**节点**有代码、父代码、标签；代码在**一版之内全局唯一**。

| 概念 | 形状 |
|---|---|
| 字典代码 | `^[a-z][a-z0-9-]{1,63}$`，进 URL 路径 |
| 版本名 | `^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`，与副表契约的 `structureVersion` 同一字符集——它会被原样写进题目属性带到引擎 |
| 节点代码 | `^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`，与副表枚举列的取值代码同一字符集（可以数字打头，GB/T 2260 就是数字） |
| 标签 | 1–200 个字符 |
| 内容摘要 | `dg1:` ＋ SHA-256 前 16 位十六进制 |

上限（三端必须一致）：

| 限 | 值 | 谁写着 |
|---|---|---|
| 一版最多几个节点 | 8000 | `DictionaryLimits.MAX_NODES` / `MAX_DICTIONARY_NODES` / `MjyDictionaryStore::MAX_NODES` |
| 最深几层 | 8 | `DictionaryLimits.MAX_DEPTH` / `MAX_DICTIONARY_LEVELS` / `MjyDictionaryStore::MAX_DEPTH` |
| 一页最多几个节点 | 200 | `DictionaryLimits.MAX_PAGE_SIZE` / `MjyDictionaryNodesEndpoint::MAX_LIMIT` |

### 内容摘要 `dg1`

```
规范串 = 每个节点一行，按**存储顺序**（即下拉框里的先后）：
         <代码>|<父代码，根为空>|<深度>|<标签>\n
dg1    = "dg1:" + hex(SHA-256(规范串 的 UTF-8 字节))[:16]
```

与副表的 `structureDigest` 刻意不同的一点：**标签进摘要**。结构摘要管的是「单元格还能不能按这一版
列定义读回来」，改标签不影响读回；字典摘要管的是「引擎上那份快照有没有被换过」，一个区改了名
就是另一份数据。顺序也进摘要——顺序是作答者看得见的内容。

摘要与版本名无关：同样的节点、不同的版本名，摘要相同。

**摘要只由平台算。** 网关与插件都不重算，只做比对（ADR 0019 决定 7）。

## 二、运营接口（`/v1/platform/dictionaries`）

整个 `/v1/platform/**` 由拦截器要求 `platform_operator` 角色。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/v1/platform/dictionaries` | `{code, name, maxDepth}` → 201 |
| `GET` | `/v1/platform/dictionaries` | 全部字典，含一版都没发布的 |
| `GET` | `/v1/platform/dictionaries/{code}/versions` | 这本字典的全部版本 |
| `POST` | `/v1/platform/dictionaries/{code}/versions` | `{version}` → 201，状态 `draft` |
| `POST` | `/v1/platform/dictionaries/{code}/versions/{v}/nodes` | `{nodes: [{code, parentCode, label}]}`，**整棵树一次**，覆盖草稿原有节点 |
| `POST` | `/v1/platform/dictionaries/{code}/versions/{v}/publish` | 算摘要、冻结、设成当前版本 |

**深度、是否叶子、排序都由平台算**，导入方声明不了——那三样要是可以声明，一份自相矛盾的导入
就会变成一棵自相矛盾的树。排序按提交顺序。

树的形状在**灌节点时**就判（孤儿、环、自己当自己的父、超深、代码重复），发布时再推一遍。

错误：`400 invalid_request`（形状、上限、树的形状）、`404 not_found`、
`409 dictionary_code_taken` / `dictionary_version_taken` / `dictionary_version_published`。

**仓库不附带任何行政区划数据集**（ADR 0019「数据从哪来」），导入是交付方的事。

## 三、租户读取接口（`/v1/dictionaries`）

只看得见**已发布**的版本；草稿等同不存在（404）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/v1/dictionaries` | 至少发布过一版的字典 |
| `GET` | `/v1/dictionaries/{code}/versions/{v}` | 这一版的状态、摘要、节点数 |
| `GET` | `/v1/dictionaries/{code}/versions/{v}/nodes?parent=&offset=&size=` | 某一层的一页；`parent` 缺省取根那一层。返回 `{nodes, total}` |
| `GET` | `/v1/dictionaries/{code}/versions/{v}/search?q=&size=` | 标签包含或代码前缀 |
| `GET` | `/v1/dictionaries/{code}/versions/{v}/nodes/{node}/path` | 从根到这个节点的整条路径，由浅到深 |

`size` 超过 200 **拒绝**（400），不悄悄截断——悄悄截断会让调用方以为「这一层就这么多」。
`q` 为空**拒绝**：那等于「把整本字典给我」。

## 四、定义里的字典引用（平台 → 网关）

### 题目

```json
{"uuid": "…", "code": "QREGION", "type": "T", "text": "所在地区",
 "theme": "mjy-cascading-select",
 "themeOptions": {
   "structureVersion": "r1",
   "dictionary": "cn-admin-divisions",
   "dictionaryVersion": "2024.1",
   "dictionaryDigest": "dg1:0123456789abcdef",
   "levels": ["省", "市", "区"]
 }}
```

**草稿里只有 `dictionary` 与 `levels` 算数。** `dictionaryVersion` 与 `dictionaryDigest`
由平台在发布时固化，草稿里写了会被 `SurveyDefinitions.normalize` 摘掉（与 `participants` 同一口径）。

### 顶层快照

```json
{"dictionaries": [
  {"code": "cn-admin-divisions", "version": "2024.1", "digest": "dg1:0123456789abcdef",
   "nodes": [["110000", "", "北京市"], ["110100", "110000", "市辖区"], …]}
]}
```

节点是**紧凑三元组** `[代码, 父代码, 标签]`，父代码为空串即根节点。几千个节点要一起塞进
1 MiB 的定义快照里，键名重复几千遍是纯浪费。

同样，草稿里的 `dictionaries` 会被摘掉；它只在发布时由平台物化。

### 网关的对账（422）

| 码 | 什么时候 |
|---|---|
| `E_DICTIONARY_MISSING` | 题目引用的 (字典, 版本) 没有随定义下发 |
| `E_DICTIONARY_DIGEST` | 下发的摘要与题目引用的摘要不一致 |
| `E_DICTIONARY_SHAPE` | 快照本身不成立（三元组形状、代码字符集、重复代码、父节点不在这一版里、超过节点上限、同一版下发两次） |
| `E_DICTIONARY_DEPTH` | 题目声明的级数多过这本字典实际的层数——作答者永远填不满最后一级 |
| `E_DICTIONARY_UNUSED` | 下发了却没有任何题目引用 |

题目自己的 `themeOptions` 有问题时（`E_THEME_*`）不再报 `E_DICTIONARY_UNUSED`：
那道题本来要用哪本字典无从得知，再报一条只是噪音。

## 五、快照怎么到引擎（网关 → 插件）

编进 `.lss` 的 `plugin_settings` 一行（引擎导入只读 `name`/`key`/`value`，写进
`lime_plugin_settings`，model=Survey）：

```
name  = MjyQuestionExtensions
key   = dictionaries
value = {"v":1,"dictionaries":[{"code":…,"version":…,"digest":…,"nodes":[[…],…]}]}
```

`value` 列是 `mediumtext`（16 MB），三千多个节点约 120 KB。

**这是运输方式，不是查询方式。** 插件首次用到时把它物化进两张带索引的表：

```
{prefix}mjyquestionextensions_dict_version
  dictionary_code string(64), dictionary_version string(32), digest string(32),
  node_count integer, updated_at datetime
  唯一索引 (dictionary_code, dictionary_version)

{prefix}mjyquestionextensions_dict_node
  dictionary_code string(64), dictionary_version string(32),
  node_code string(32), parent_code string(32) 默认 ''（根节点）,
  depth integer, label string(200), sort_key integer, search_text string(200)
  唯一索引 (dictionary_code, dictionary_version, node_code)
  索引     (dictionary_code, dictionary_version, parent_code, sort_key)
```

`parent_code` 根节点存**空串而不是 NULL**：SQL 里 `= NULL` 永远不成立，用空串可以让
「取根那一层」和「取某个节点的子级」走同一条语句。

物化**按摘要幂等**：装过的同一摘要什么都不做；摘要不一致就整版换掉，绝不与旧节点混在一起——
混在一起会让「跨版本的节点」这类篡改变成合法路径。

## 六、副表的列定义：新的列类型 `dict`

```json
{"code": "L1", "label": "省", "type": "dict", "required": true,
 "dictionary": "cn-admin-divisions", "dictionaryVersion": "2024.1", "level": 1}
```

与 `enum` 的区别只有一条：**取值集合不在列定义里**，由 (字典, 版本, 层) 决定。
`dict` 列不得声明 `options`。

`dictionaryVersion` 必须在**列定义里**，而不是另外查一次题目属性：单元格是按哪一版写下的，
跟结构版本一样属于**结构**，它必须随列字典一起被读端看到。

### 结构摘要

`dict` 列在原有八个字段**之后**追加一段：

```
<代码>|<类型>|<必填>|<maxLength>|<min>|<max>|<枚举取值,逗号分隔>|<distinct>|<字典>@<字典版本>
                                                                ^^^^^^^^^^^^^^^^^^^^^^ 只有 dict 列有
```

因此既有八个副表题型的 `sd1:` 摘要**逐字节不变**。

### 服务端判定的三条规则

`MjyRepeatingTableValidator::checkDictionaryPaths()` 把同一 (字典, 版本) 的 `dict` 列按 `level`
升序排好，逐级核对：

1. 这一级的代码在**这一版**字典里（跨版本的节点因此进不来）；
2. 它的层深**正好**是这一级（跳级因此进不来）；
3. 它的父节点**恰好**是上一级（跨省的市因此进不来；第一级的父必须为空，即必须是根）。

**查不到就拒**（失败关闭）：引擎上没装这一版、或根本没有字典可用时，这道题谁也交不了。
那正是应有的结果——发布期的对账本就不该让这种问卷发得出去。

单元格的字符集在查字典**之前**先守住（`MjyTableColumnSpec::VALUE_CODE_PATTERN`）：
能当参数用的值不该变成一次查询。

## 七、作答页取一层（作答者 → 插件）

```
GET <engine>/index.php/plugins/direct
  ?plugin=MjyQuestionExtensions
  &function=dictionaryNodes
  &sid=42
  &dictionary=cn-admin-divisions
  &version=2024.1
  &parent=440000      （可选，缺省取根那一层）
  &offset=0           （可选）
  &limit=200          （可选，上限 200）
```

参数集合是**封闭白名单**：出现任何其他参数即 400。

**这条端点没有签名**，与 [plugin-channel-v1](plugin-channel-v1.md) 那条网关通道是两回事
（作答者手里没有通道密钥）。因此两条规矩刻意不照搬：不统一成一个 401（读的是公共参考数据，
区分错误不泄漏任何东西），不接 `MjyChannelRateLimit`（那张表是落库的，接在无签名端点上
等于亲手造出 ADR 0018 决定 6 警告过的放大面）。

**授权**：只服务「这份问卷真的有一道题引用了这本字典的这一版」（按题目属性
`mjy_dictionary` / `mjy_dictionary_version` 连接查询）。所以它给不出任何已发布问卷
没有向作答者展示过的东西。

| 状态 | 体 | 含义 |
|---|---|---|
| `200` | `{"dictionary","version","parent","nodes":[{"code","label","depth"}],"total"}` | 读到了（包括空） |
| `400` | `{"error":"bad_request"}` | 参数白名单或形状不符。**此前没有任何数据库访问** |
| `404` | `{"error":"not_found"}` | 这份问卷没有引用这本字典的这一版 |
| `500` | `{"error":"unavailable"}` | 插件内部故障，原因只在服务端日志 |

所有应答都带 `Content-Type: application/json; charset=utf-8` 与 `Cache-Control: no-store`。

## 八、验证

- 平台：`cn.mjy.platform.dictionary` 的 `DictionaryLifecycleTest`、`DictionaryCatalogTest`、
  `DictionaryPinningTest`、`DictionaryApiTest`。
- 网关：`tests/test_question_cascading.py`（列生成、themeOptions 形状、绑定记录、
  快照对账、`.lss` 里的 `plugin_settings`）。
- 插件：`MjyDictionaryStoreTest`（物化与查询）、`MjyDictionaryPathTest`（**篡改用例**）、
  `MjyDictionaryNodesEndpointTest`（形状、授权、分页）。
- 真引擎端到端：`platform/deploy/test/run-question-themes.sh`，双库各 278 项。
  两道多级下拉题分别引用同一本字典的两版，证明两版在引擎上共存且互不串味；
  场景 D 的七条篡改各断言「被拒且留在本页」与「这一轮没有留下已提交答卷」。
- 三端上限一致性目前靠注释互指，**没有自动化的一致性检查**（与 `STRUCTURED_THEMES` 的
  parity 测试不同），属于已知缺口。
