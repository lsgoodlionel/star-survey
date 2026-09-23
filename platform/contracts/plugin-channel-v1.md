# 契约：发布网关 → 引擎插件 鉴权通道（v1）

网关侧 `pubgw/channel.py` 调用引擎侧 `MjyQuestionExtensions::newDirectRequest()`。
依据 [ADR 0018](../docs/adr/0018-gateway-plugin-channel.md)；副表的列与语义见
[`question-extension-tables-v1.md`](question-extension-tables-v1.md)，本文只管**怎么把它取出来**。

状态：v1（WP-06.4 引入）。**改动须同步两端并升版本**：签名串、参数集合、错误码都是两端约定。

这是一条**新的信任边界**。它与「平台 → 网关」那条（[`publish-gateway-v1.md`](publish-gateway-v1.md)）
是两条独立的边界，密钥不同、签名头不同、拒绝语义不同——本文把差异写在明处。

## 部署与信任边界

### 密钥

```
实例密钥 = hex(HMAC-SHA256(平台主密钥, "mjy-engine-events/v1/" + engineInstanceId))   // ADR 0003，已存在
通道密钥 = hex(HMAC-SHA256(实例密钥的 ASCII 字节, "mjy-plugin-channel/v1/" + engineInstanceId))
```

| 端 | 配置 | 说明 |
|---|---|---|
| 引擎（插件） | `MJY_PLATFORM_EVENTS_SECRET` | 已存在（`MjyPlatformBridge`）。插件**自己派生**通道密钥，无新增变量 |
| 引擎（插件） | `MJY_PLATFORM_EVENTS_SECRET_PREVIOUS` | 可选，仅轮换期间存在。插件同时接受它派生出的通道密钥 |
| 网关 | 引擎配置项 `channelSecretEnv` → 环境变量 | 只有通道密钥，**没有实例密钥**——网关被攻破也伪造不了引擎事件 |

主密钥缺失或不足 32 字节 → **不派生任何密钥，端点拒收一切**（失败即关闭）。

**密钥、签名值、完整查询串（含 `sig`）一律不得写日志**，两端都是。

### 签名

```
签名串 = "<ts>" + "." + <规范化查询串>
sig    = hex(HMAC-SHA256(通道密钥的 ASCII 字节, 签名串))        // 64 位小写十六进制
```

规范化查询串 = 除 `sig` 外的**全部**查询参数，按参数名升序（同名参数按值升序），
键与值各自 RFC 3986 百分号编码（未保留字符 `A-Za-z0-9-._~`），以 `k=v` 用 `&` 连接。
PHP 用 `rawurlencode`，Python 用 `urllib.parse.quote(s, safe="")`——两者对 RFC 3986 的定义一致。

签名覆盖 `plugin` 与 `function`，因此签好的请求**改投不到别的插件函数**；
覆盖 `sid` 与 `responseIds`，因此**改投不到别人的答卷**。

`ts` 是 Unix 秒，偏差超过 **±300 秒**即拒绝（与 `pubgw/auth.py` 和 `response-read-v1.md` 同一个数字）。

**不做一次性随机数。** 本端点只读且幂等，重放拿到的是重放者本就拿到过的同一页；
改投由「签名覆盖整个查询串」挡住，长期有效由 ±300 秒挡住。传输层强制 HTTPS。
**本通道不得承载任何有副作用的操作**——要加写操作，先补随机数存储并重开 ADR 0018 决定 4。

### 为什么是 GET

`plugins/direct` 的 POST **到不了插件**：`application/config/internal.php:153` 的
`noCsrfValidationRoutes` 只豁免 `rest`、`admin/remotecontrol`、`plugins/unsecure`，
POST 会先被 Yii 的 CSRF 校验挡下，事件根本不派发。代价：`sid`、代次、答卷号进 access log。
**答案值只在响应体里，永不进 URL。**

## GET `<engine>/index.php/plugins/direct`

```
?plugin=MjyQuestionExtensions
&function=extensionAnswers
&sid=42
&generation=3f2a…（代次）
&responseIds=1001,1002,1003
&questionCodes=TABLE1,TABLE2
&ts=1800000000
&sig=<64 hex>
```

参数集合是**封闭白名单**：出现任何其他参数即拒绝（否则攻击者可以追加一个不在签名里的参数来改变行为）。

| 参数 | 约束 |
|---|---|
| `plugin` | 固定 `MjyQuestionExtensions` |
| `function` | 固定 `extensionAnswers` |
| `sid` | 正整数，≤ 10 位 |
| `generation` | `[A-Za-z0-9][A-Za-z0-9._-]{0,35}` |
| `responseIds` | 逗号分隔的正整数，**严格升序、互不相同**，1–200 个 |
| `questionCodes` | 逗号分隔，每个 `[A-Za-z0-9_]{1,64}`，互不相同，1–50 个 |
| `ts` | 1–12 位数字 |
| `sig` | 64 位十六进制 |

`responseIds` 要求严格升序是为了让签名串**规范**：同一组答卷号只有一种写法，
两端不会因为顺序不同算出不同的签名。

### 应答

| 状态 | 体 | 含义 |
|---|---|---|
| `200` | 见下 | 读到了（**包括「一行都没有」**） |
| `401` | `{"error":"unauthorized"}` | **验签之前的一切拒绝，全部是这一个应答**（见下） |
| `400` | `{"error":"page_too_large"}` | 验签通过，但这一页的单元格超过 20000；缩小 `responseIds` 重试 |
| `429` | `{"error":"rate_limited"}` | 验签通过，但超出速率额度；不说明额度细节 |
| `500` | `{"error":"unavailable"}` | 验签通过，插件内部故障；原因只在服务端日志 |

所有应答都带 `Content-Type: application/json; charset=utf-8` 与 `Cache-Control: no-store`。

**401 是统一拒绝**：签名头缺失、时间戳格式错、时间戳过期、签名格式错、签名不匹配、
通道密钥未配置、参数白名单不符、参数越界——全部返回**同一个状态、同一段响应体**，
原因只进服务端日志。理由见 ADR 0018 决定 5（飞书回调那次的填充预言机）。
**调用方不得试图从 401 推断原因，也不得把 401 当作「问卷不存在」。**

`sid` 不存在或代次不匹配 → **`200` 加空 `answers`**，不是错误：
问卷停用再激活后平台的代次判定会慢一拍（ADR 0013 决定 3），那是正常的空。

### 200 的体

```json
{
  "plugin": "MjyQuestionExtensions",
  "engineInstanceId": "hd-engine-01",
  "surveyId": 42,
  "generation": "3f2a…",
  "answers": {
    "1001": {
      "TABLE1": {
        "structureVersion": "v3",
        "isValid": true,
        "rows": [
          {"item": "甲", "qty": "2"},
          {"item": "乙", "qty": "3"}
        ]
      }
    }
  }
}
```

- `answers` 的键是**答卷号的十进制字符串**（JSON 对象键只能是字符串）。
- 没有任何副表数据的答卷**不出现**在 `answers` 里；一个都没有时 `answers` 是 `{}`。
- `rows` 按 `row_index` 升序，**下标即行序**；`row_index` 有空洞时收紧成连续下标
  （与 `MjyStructuredAnswerStore::fetchRows()` 的既有语义一致）。
- 单元格值一律是字符串；`cell_value` 为 `NULL` 时是 `""`。
- `structureVersion` 取自 `answer_state`；读端**必须**据它去取对应版本的列字典，
  再解释 `rows` 的列代码（见 `question-extension-tables-v1.md` 二）。`"0"` 表示无从考证，
  只能按列代码原样呈现。
- `isValid` 来自 `answer_state.is_valid`。**`errors` 列不出现在应答里**——
  它是给作答者看的拒绝理由，不是导出内容。

## 引擎侧（插件）必须做到

- **验签之前不碰数据库、不碰任何作答**。派生密钥只读环境变量，验签只做一次进程内 HMAC。
- 限流**在验签之后**。放在之前就得为匿名请求维护落库的计数器，亲手造出放大面（ADR 0018 决定 6）。
- 签名比较用定时安全比较（`hash_equals`）。轮换期的两个密钥都要逐个比完，不得短路。
- **结果体量绝不静默截断**：超限返回 400，不返回少几行的 200。
- 日志可记：实例、sid、代次、答卷号**区间**、题目代码、行数、判定与原因码。
  **不得记**：任何 `cell_value`、`errors` 内容、密钥、签名、完整查询串。

## 网关侧必须做到

- 只对绑定记录里**有 `sideTable` 的题目代码**发起调用（`pubgw/fieldmap.py:53`）。
  网关自己不存绑定，题目代码由平台随读取请求传下来（`extensionQuestions`）。
- **失败关闭**：本通道任何非 200，整页 `POST /v1/responses/read` 返回
  `502 engine_error`（与该端点既有的引擎失败一个口径，不另造状态码），
  绝不返回「看着完整、其实缺了扩展题」的一页。平台再据此对自己的调用方
  报 `503 answers_unavailable`（ADR 0013 决定 8）。
- 引擎没配通道密钥而平台又点名了扩展题 → 同样失败关闭，不得安静地少返回。
- 不把 401 当作「问卷不存在」而静默当空处理——那会让一次密钥配错变成一次静默的数据缺失。
- 日志只记实例、sid、条数与失败类型，**从不记值**（沿用 `pubgw/responses.py` 的自律）。

## 验证

- 网关侧单元测试：`platform/tools/publish-gateway/tests/test_channel.py`
  （签名向量、规范化查询串、失败关闭、不记值）。
- 插件侧 PHPUnit：`plugins/MjyQuestionExtensions/tests/MjyChannelAuthTest.php`（验签与统一拒绝）、
  `MjyExtensionAnswerEndpointTest.php`（端到端读取、代次隔离、体量上限、限流）。
- 跨语言签名一致性：两侧各有一组**同一份**固定向量，任一端改了算法都会红。
