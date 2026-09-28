# 契约：平台 → 发布网关 答卷读取（v1）

平台业务服务（`cn.mjy.platform.response.HttpResponseAnswerSource`）按页向网关（`pubgw/responses.py`）读取答卷作答值。
设计与取舍见 ADR 0013。改动须同步两端并升版本。

## 部署与信任边界

与 `publish-gateway-v1.md` 完全相同：同一网关进程、同一共享密钥（平台 `PLATFORM_PUBGW_SECRET` / 网关 `PUBGW_SHARED_SECRET`）、
同一签名规则（`X-Pubgw-Timestamp`、`X-Pubgw-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<原始请求体>"))`，±300 秒）。
**引擎口令只在网关。** 网关不做业务授权：平台必须先完成权限判定，且只传本租户已发布版本里记录的 `(engineInstanceId, surveyId)`。

## `POST /v1/responses/read`

请求体（`Content-Type: application/json`，≤ 1 MiB），四个必填字段，外加三个可选字段
（`includeRespondent` 与 `generation`＋`extensionQuestions` 互相正交，可同时出现）：

```json
{
  "engineInstanceId": "hd-engine-01",
  "surveyId": 900001,
  "responseIds": [1, 3, 999],
  "fields": ["Q1", "Q1_Cother", "Q5_S8#0"],

  "includeRespondent": true,

  "generation": "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
  "extensionQuestions": ["TABLE1"]
}
```

- `surveyId`：引擎 sid，正整数。
- `responseIds`：1–500 个互不相同的正整数。
- `fields`：1–5000 个互不相同的引擎答卷列名（`[A-Za-z0-9_#]{1,64}`），不得包含 `id`，
  **也不得包含 `token`**（400）：令牌只能经 `includeRespondent` 拿，当普通列请求就绕过了匿名判定。
  `includeRespondent` 为真、**或**点名了 `extensionQuestions` 时**可以为空数组**
  （只要"这份答卷是谁交的"或只要扩展表作答，不必多请求一列无关作答，`values` 回空对象）；
  否则至少一列。
- `includeRespondent`（可选，布尔，缺省 `false`）：是否一并回答"这份答卷是用哪个邀请码答的"
  （ADR 0016 缺口 (b)）。不是布尔即 400。
- `generation`（可选）：代次，`[A-Za-z0-9][A-Za-z0-9._-]{0,35}`。
- `extensionQuestions`（可选）：≤ 50 个互不相同的题目代码（`[A-Za-z0-9_]{1,64}`），
  即绑定记录里带 `sideTable` 的那些题。网关自己不存绑定，所以由平台点名。

`generation` 与 `extensionQuestions` **要么都给要么都不给**：只给题目代码而不给代次 → 400。
代次是副表自然键的一段，没有它无法保证不串代次（ADR 0013 决定 3），宁可拒绝也不读。
出现上述之外的任何字段 → 400。

**排序题（`R`）的名次列**在引擎答卷表里没有物理列，值由网关解析主列 JSON 得到（`pubgw/ranking.py`）：
名次列 *n* 的值是排在第 *n* 位的项代码，没排到的位置是空串 `""`，整题不适用时（未到达、被条件隐藏）仍为 `null`。
平台照常按列名请求，不必知道哪些列是名次列；网关为此每份问卷读一次 `get_fieldmap`（按 `(实例, sid)` 缓存）。

网关按答卷号升序切成间距 < 200 的区间，逐段调用 RemoteControl
`export_responses(sid, "json", 默认语言, "all", "code", "short", 区间下界, 区间上界, ["id", ...fields])`，
按请求列的顺序做位置对应（列数不符即判引擎应答不可信）。

响应：

| 状态 | 体 | 含义 |
|---|---|---|
| 200 | `{"responses":[{"id":1,"values":{"Q1":"A1","Q1_Cother":null}}],"missing":[999]}` | `responses` 按答卷号升序，只含请求的答卷；`values` 恰好是请求的列，值为文本或 `null`；引擎当前答卷表里没有的答卷列在 `missing` |
| 400 | `{"error":"invalid_request"}` | 请求体或 Content-Type 不合法 |
| 401 | `{"error":"<原因>"}` | 认证失败，不做任何引擎调用 |
| 404 | `{"error":"unknown_engine_instance"}` | 网关没有这个实例的配置 |
| 502 | `{"error":"engine_error"}` | 引擎登录被拒、RPC 失败、导出结果不是 base64 JSON 或列数不符；**插件通道不可达或未配置**；不带引擎错误原文 |
| 500 | `{"error":"internal_error"}` | 网关意外异常，不含堆栈 |

`GET /v1/responses/read` → 405（`Allow: POST`）。响应头同发布端点：`Cache-Control: no-store`。

### 参与者令牌（`includeRespondent`，ADR 0016 缺口 (b)）

`includeRespondent: true` 时每条 `responses[]` 多一个 `token` 键：

```json
{"responses": [{"id": 1, "values": {"Q1": "A1"}, "token": "a1b2c3d4e5f6g7h8"}], "missing": []}
```

不传这个字段时应答形状与本节之前逐字节一致（不多这个键，也不会多问引擎一次问卷属性）。

- 为什么需要：引擎事件与答卷投影里只有答卷号，**没有令牌**，平台无法判断"张三答完了没有"。
  发布回执的 `invitations[]` 给出"哪个码发给了谁"，这里给出"一份答卷属于哪个码"，两半合起来
  催答才能按人生效。
- **匿名问卷永不给令牌**：要了也是 `null`。引擎只在非匿名时给答卷表建 `token` 列
  （`SurveyActivator.php:253`），但问卷可以先以非匿名激活、把列和值都写好，之后再把
  `anonymized` 改成 `Y`——列和数据都还在。所以网关按问卷**当前**的 `anonymized` 判定
  （只有 `Y` 算匿名，与引擎 `Survey::isAnonymized` 一致），而不是按列在不在。
- 判定不出来（`get_survey_properties` 失败、或没有这个设置）就**不给**（`null`），不照发。
- 该答卷没有用令牌进场（非匿名卷也可能有匿名作答）时那一列是空串，一律归为 `null`。

### 扩展表作答（`extensionAnswers`）

请求带了 `generation`＋`extensionQuestions` 时，200 的体里多一段 `extensionAnswers`
（其余字段一字不变；与 `includeRespondent` 同时出现时，`responses[].token` 与这一段各自照常给出）：

```json
{
  "responses": [{"id": 1, "values": {"Q1": "A1"}}],
  "missing": [999],
  "extensionAnswers": {
    "1": {
      "TABLE1": {
        "structureVersion": "rt3",
        "isValid": true,
        "rows": [{"item": "甲", "qty": "2"}, {"item": "乙", "qty": "3"}]
      }
    }
  }
}
```

**为什么单列一段而不并进 `values`**：副表作答是行×列，且行数逐份答卷不同。
压进扁平列需要一个「最多多少行」的约定，而那个上限在读取时并不知道——
字段字典是发布时冻结的，副表行数是作答时才产生的。硬压要么截断作答，要么让列集随数据漂移。

- 键是答卷号的十进制字符串；没有副表数据的答卷不出现，一个都没有时是 `{}`。
- `rows` 下标即行序；单元格值一律是字符串，`NULL` 为 `""`。
- `structureVersion` 决定用哪一版列字典解释列代码，`"0"` 表示无从考证
  （见 [`question-extension-tables-v1.md`](question-extension-tables-v1.md)）。
- 值来自网关↔插件鉴权通道（[`plugin-channel-v1.md`](plugin-channel-v1.md)），
  **失败关闭**：通道任何非 200 或该引擎没配通道密钥，整页 502，绝不少返回。

## `POST /v1/responses/attachment`（附件取件）

一次取**一份**上传附件的字节（R06-07 的打包那一半，ADR 0015 增补四）。
认证与签名规则与 `/v1/responses/read` 完全相同（`X-Pubgw-Timestamp` ＋ `X-Pubgw-Signature`，
签名覆盖原始请求体）。

```json
{
  "engineInstanceId": "hd-engine-01",
  "surveyId": 42,
  "generation": "3f2a…",
  "responseId": 1001,
  "field": "123456X7X8",
  "storedName": "fu_1a2b3c",
  "maxBytes": 67108864
}
```

字段集合是**封闭的**：多一个、少一个都是 400。

| 字段 | 约束 |
|---|---|
| `surveyId` / `responseId` / `maxBytes` | 正整数；`maxBytes` 不超过网关硬顶 512 MiB |
| `generation` | `[A-Za-z0-9][A-Za-z0-9._-]{0,35}` |
| `field` | 引擎答卷表的列名 `[A-Za-z0-9_#]{1,64}`；`token` 一律拒绝（它不是上传列） |
| `storedName` | 引擎生成的存储名 `[A-Za-z0-9][A-Za-z0-9._-]{0,254}`，且不含 `..` |

**只按引擎生成的存储名取件。** 作答者起的原始文件名根本不传到这条链路上，
因此不会出现在网关或引擎的任何一段访问日志里（与 ADR 0018 决定 1「元数据可以进 URL、
作答内容不行」同一条理由）。

### 应答

| 状态 | 体 | 含义 | 平台怎么办 |
|---|---|---|---|
| `200` | **字节流**，`Content-Type: application/octet-stream`，带 `Content-Length` | 取到了 | 落盘 |
| `400` | `{"error":"invalid_request"}` | 请求体不合契约 | 修请求，不重试 |
| `401` | `{"error":"<reason>"}` | 平台→网关的签名不过 | 修配置 |
| `404` | `{"error":"unknown_engine_instance"}` | 没有这台引擎 | 修配置 |
| `404` | `{"error":"not_found"}` | 引擎里已经没有这一份 | 记为缺失，**不重试** |
| `413` | `{"error":"too_large"}` | 超出 `maxBytes` | 记为超限，**不重试** |
| `502` | `{"error":"engine_error"}` | 通道不可达、没配通道密钥、应答不可信 | 退避后重试，**绝不**记为缺失 |

**404 与 502 的分界是这条端点最要紧的一条。** 把通道故障当成「这一份不在了」，
等于让一次网络抖动变成一份悄悄缺失的附件——比报错危险得多。反过来把「确实不在了」
当成故障，会让一份早已被清理的附件把整个导出作业拖到重试上限。

`not_found` **不区分**是代次不符、答卷不在、这一列里没有这个存储名，还是文件已被清理：
对调用方来说它们是同一件事，区分它们只会多给持有密钥的一方一件能探测的事。

### 两端都必须做到

- **流式**：网关与引擎都只逐块转手（64 KiB），整份文件从不进内存。
  这正是 RemoteControl `get_uploaded_files` 做不到的事——它按一份答卷把全部文件
  base64 塞进一个 JSON 应答。
- **绝不截断**：超限返回 413，不返回半份文件。
- 引擎侧必须核对「这份文件真的列在这份答卷的这一列里」，只靠文件名语法不够——
  那样持有通道密钥的一方就能按名字翻遍整个问卷的上传目录。
- 日志只记实例、sid、答卷号与**存储名**，不记内容、不记原始文件名、不记密钥或含 `sig` 的 URL。

## 平台侧必须做到

- 只在 `view-raw-responses` 判定通过后调用；敏感列遮蔽在平台侧完成（网关返回原值）。
- 附件取件只对**导出作业计划里冻结的**(实例, sid, 代次) 发起；敏感上传列被遮蔽后清单里没有行，
  因此也不会去取它的字节。
- 只为投影状态非删除、且属于该 sid 最近代次的答卷调用（ADR 0013 决定 3）。
- 应答里出现未请求的答卷号、重复答卷号或形状不对，一律视为不可信（503），不返回部分结果。
- 非 200 一律视为"暂时取不到作答"（503），不重试写操作——本端点只读、天然幂等，调用方可直接重试。
- 请求与应答正文含个人数据，两端都不记录正文。

## 验证

- 网关单测：`platform/tools/publish-gateway/tests/test_responses.py`（假引擎按 7.1.2 导出形状作答）、
  `tests/test_ranking.py`（名次解析、投影与列结构缓存）、
  `tests/test_response_extensions.py`（扩展表作答单列一段、扁平字段不受影响、失败即关闭）。
- 真引擎：`TEST_DB=mysql|pgsql platform/deploy/test/run-response-read.sh`（多列题、"其他"、评论、双尺度逐列核对）；
  排序题的名次在 `run-question-types.sh` 场景 D2（HTTP 真实作答后逐名次核对）。
- 平台：`HttpResponseAnswerSourceTest`（签名逐字节、应答解析、失败即关闭；扩展表作答的请求体、
  解析与「缺这一段／没点过名的答卷或题目／`rows` 形状不对」一律不可信）。
  扩展表作答进导出见 ADR 0015 增补二（`ResponseExportExtensionsTest`）。
- 附件取件：网关单测 `tests/test_attachments.py`（签名覆盖整个查询串、逐块流式、
  404／413／502 的分界、HTTP 外壳把字节原样写出）；
  平台侧 `ExportAttachmentFetcherTest` 与 `ResponseExportAttachmentPackageTest`
  （逐份落盘、已有结论跳过、缺失记下不拖垮整包、暂时失败重试）。
