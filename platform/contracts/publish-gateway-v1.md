# 契约：平台 → 发布网关（v1）

平台业务服务（Java）通过这个内部 HTTP 接口，请发布网关（Python，`platform/tools/publish-gateway`）
把一份问卷定义发布到某个引擎实例。两端各自以本文件为准实现与测试；改动须同步两端并升版本。

> 向后兼容增补 [v1.2](publish-gateway-v1.2.md)：`POST /v1/close`（收口被取代的版本）与 `POST /v1/drift-check`（漂移检查）。

## 部署与信任边界

- 网关是内部服务，只接受平台调用，不对公网开放。
- **引擎管理员口令只存在网关侧**：网关按配置把 `engineInstanceId` 解析为 RemoteControl 地址、账号与口令（口令来自环境变量）。平台只传实例 id，从不持有、也不转发引擎口令。
- 请求用共享密钥做 HMAC 认证（与引擎事件同一风格，但**不同密钥**）：
  - 平台侧环境变量 `PLATFORM_PUBGW_SECRET`，网关侧 `PUBGW_SHARED_SECRET`，值相同，至少 32 字节；
  - 请求头 `X-Pubgw-Timestamp`（Unix 秒）与 `X-Pubgw-Signature` = `hex(HMAC-SHA256(secret, "<timestamp>.<原始请求体>"))`；
  - 时间戳偏差超过 ±300 秒、签名不符、缺头一律 401，且不做任何引擎调用。

## `POST /v1/publish`

请求体（`Content-Type: application/json`，≤ 1 MiB）：

```json
{
  "requestId": "0b0d3f2e-...",
  "engineInstanceId": "hd-engine-01",
  "definition": { "...": "网关现有的问卷定义格式，见 platform/tests/fixtures/surveys/publish-gateway.json" }
}
```

- `requestId`：平台生成的幂等键（UUID）。同一 `requestId` 重复到达时，网关返回首次的结果，不再发布第二次。
- 同一 `definition.uuid` 在同一实例上**同一时刻只允许一次发布**；并发的第二个请求得到 409 `publish_in_progress`。

响应：

| 状态 | 体 | 含义 |
|---|---|---|
| 200 | `{"status":"published","result":<PublishResult>}` | 已激活，`result.binding` 为绑定记录 |
| 422 | `{"status":"rejected","result":<PublishResult>}` | 定义未通过前置校验（`failedStage` = `validate`），**引擎未被触碰** |
| 502 | `{"status":"failed","result":<PublishResult>}` | 引擎侧失败；`rolledBack` 表明是否已回滚，`orphanSurveyId` 非空表示回滚也失败、需人工处理 |
| 409 | `{"status":"conflict","error":"publish_in_progress"}` | 同一定义正在发布 |
| 404 | `{"error":"unknown_engine_instance"}` | 网关没有这个实例的配置 |
| 400 | `{"error":"invalid_request"}` | 请求体不合法 |
| 401 | `{"error":"<原因>"}` | 认证失败 |
| 500 | `{"error":"internal_error"}` | 网关意外异常；不含堆栈。结果也会按 `requestId` 存档，同一 `requestId` 重试不会再发布一次，平台应按"待核对"处理 |

### 实现澄清（v1.1，2026-09-22，网关实现时确定，平台端须一致）

- **400 `invalid_request`** 涵盖：请求体超过 1 MiB（在读取正文之前拒绝）、无长度的分块请求、结构性错误的定义（缺 `uuid`、`definitionVersion` 不符）、`requestId` 不是规范 UUID、顶层出现三个字段之外的键、`Content-Type` 不是 `application/json`、**同一 `requestId` 但请求体不同**（不发布）。
- **422** 仅用于"能解析、但未通过校验"的定义；`failedStage` 为 `validate` 或 `compile` 时都属此类，此时引擎都未被触碰。其余引擎侧失败（包括引擎登录被拒）一律 502。
- **409** 同时覆盖两种情况：同一 `requestId` 的首个请求仍在进行中；同一实例上同一 `definition.uuid` 正在发布。
- 网关的并发锁在进程内，**只能单副本运行**；多副本需要共享锁（留待生产化）。
- 网关会把所有已配置的引擎口令在响应体与日志中替换为 `***`，即使引擎在错误信息里回显了口令。

### 邀请码回读（v1.2，2026-09-23，ADR 0016）

定义带 `participants` 时，发布回执多一个 `invitations` 键；不带参与者的发布与本节之前逐字节一致。

```json
"invitations": [
  {"index": 0, "ref": "contact-7", "token": "a1b2c3d4e5f6g7h8", "tid": "1"},
  {"index": 1, "ref": null,        "token": "h8g7f6e5d4c3b2a1", "tid": "2"}
]
```

- 网关的 `add_participants` 固定 `create_token=true`，**定义里写的 token 会被引擎替换**，
  平台要发的邀请码只能这样拿。
- `index` 是定义里 `participants` 的下标，数组与定义**同序同长**。对应关系只能按位置：
  引擎逐条按引用原地替换提交数组，成功的条目被整条换成参与者表行属性，平台传的非列字段
  已被丢弃，姓名邮箱在开了字段加密的问卷上是密文——按这些字段对不回来。
- `participants[].ref` 是**平台自选的不透明引用**（可选，字符串，同一定义内不得重复，不得为空，
  否则 422）。网关摘掉它、不转发给引擎，只在回执里原样回显。没写的回显 `null`，按 `index` 对应。
- 邀请码配不齐时**发布失败并回滚**（502，`failedStage` = `activate`）：返回行数与提交不一致、
  某一条没有 token、两条拿到同一个 token，都属此类。平台拿不到码却以为发布成功会登记路由、
  发出一批打不开的邀请，且无从察觉。
- `invitations` 会随回执按 `requestId` 一起存档，所以**网关状态目录里存着已签发的邀请码明文**；
  存储目前没有保留期（遗留）。

`<PublishResult>` 即网关现有 `PublishResult.to_dict()` 的结构（`ok`、`surveyId`、`failedStage`、`failures`、`rolledBack`、`orphanSurveyId`、`steps`、`binding`、`verification`）。`binding` 即 `BindingRecord.to_dict()`：`engineInstance`、`surveyId`、`definitionUuid`、`compilerVersion`、`fingerprintVersion`、`fingerprint`、`language`、`publishedAt`、`questions[]`。

## `GET /healthz`

无需认证，200 `{"status":"ok"}`。不暴露实例列表或任何配置。

## 平台侧必须做到

- 发布前在平台侧完成权限判定（`publish` / 审批），网关不做业务授权；
- 以数据库行锁保证同一问卷同一时刻只有一个发布在进行，并生成 `requestId`；
- 需要邀请码的定义（`policy.access.invitationRequired`）在固化快照时写入 `participants[]`，每条只带
  `ref`（本平台用联系人 id，见 ADR 0017 决定 8）；不需要邀请码时**不出现这个键**；
- 200 时持久化绑定记录与指纹，并登记公开路由（公开 UUID ↔ 引擎实例 ↔ sid）；定义带了几个参与者，
  回执就必须回来几条 `invitations`，对不上按发布失败处理（不登记路由）；邀请码只写进平台的
  联系人↔参与者映射表，不进审计、日志与定义快照；
- 422 / 502 时记录失败阶段与原因，问卷状态回到可再次发布，**不登记路由**；
- 超时或网络错误：结果未知，状态记为"待核对"，用同一 `requestId` 重试，依赖网关幂等拿到确定结果。
- 409、500 及无法解析的响应同样按"结果未知"处理（v1.1）；400 / 401 / 404 记为发布失败，重试时换新的 `requestId`。
