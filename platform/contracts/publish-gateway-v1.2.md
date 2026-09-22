# 契约增补：平台 → 发布网关（v1.2，2026-09-22）

本文件是 [publish-gateway-v1.md](publish-gateway-v1.md) 的**向后兼容增补**，为 WP-01 的重新发布（01.3）
与漂移检测（01.4）服务，决定见 [ADR 0012](../docs/adr/0012-republish-and-drift.md)。

- `POST /v1/publish`、`GET /healthz` 的行为、请求体与应答**一字不改**；v1.1 的全部澄清继续有效。
- 新增两个接口，部署、信任边界、HMAC 认证（同一密钥、同样的请求头与 ±300 秒）、1 MiB 请求体上限、
  `Content-Type: application/json`、口令脱敏（`***`）与单副本限制都与 `/v1/publish` 相同。
- 为什么不升 v2：没有任何既有字段的含义变化，只多了两个路径；旧平台不调用它们即可照旧工作。

## 重新发布不需要新接口

重新发布一个已上线问卷的新版本，就是用**同一个 `definition.uuid`、新的 `requestId`** 再调一次
`POST /v1/publish`。网关因此在引擎里**新建**一份问卷（新 sid）并走完七个阶段；旧 sid 不被触碰。
失败时的回滚（`delete_survey`）只作用于本次新建、尚未收过任何答卷的 sid，所以对重新发布同样安全。
旧版本何时停止接收新答卷，由平台在切换路由之后调用 `POST /v1/close` 决定。

## `POST /v1/close`

让一份**已被新版本取代**的引擎问卷不再接收新的答卷。

```json
{ "requestId": "5b1e…", "engineInstanceId": "hd-engine-01", "surveyId": 700123 }
```

- 三个字段缺一不可、不得多出；`requestId` 为规范 UUID，**只用于日志关联**；`surveyId` 为正整数。
- 做法是设置问卷的过期时间（`expires`）为网关 UTC 当下回拨 24 小时，**不停用、不删除**：答卷表、
  参与者表与结构全部保留。已经过期的问卷不再改写（保留更早的时刻）。
- 天然幂等：重复调用无害，因此**不进 requestId 结果存档**；同一 `(实例, surveyId)` 同一时刻只处理一个请求。
- 网关不做业务判断（例如"这是不是当前在线的版本"）——那是平台的责任。

| 状态 | 体 | 含义 |
|---|---|---|
| 200 | `{"status":"closed","result":{"surveyId":700123,"expires":"2026-09-21 08:30:00","alreadyClosed":false}}` | 引擎回读确认已过期；`alreadyClosed` 为真表示调用前就已过期、本次未改写 |
| 502 | `{"status":"failed","error":"E_SURVEY_MISSING","detail":"…"}` | 引擎里没有这个 sid |
| 502 | `{"status":"failed","error":"E_CLOSE_NOT_APPLIED","detail":"…"}` | 引擎没有存上过期时间（例如管理员设了更晚的开始时间） |
| 502 | `{"status":"failed","error":"engine_error","detail":"…"}` | 其他引擎侧失败（登录被拒、传输错误等），`detail` 已脱敏 |
| 409 | `{"status":"conflict","error":"close_in_progress"}` | 同一 sid 的收口正在进行 |
| 400 / 401 / 404 / 500 | 同 v1 | 请求体不合法 / 认证失败 / 未知实例 / 网关意外异常 |

平台侧：任何非 200 都视为"尚未收口"，稍后换新的 `requestId` 重试即可。

## `POST /v1/drift-check`

只读回引擎，判断一份已发布问卷的结构是否仍是发布时的样子（有人在引擎管理端改过它就是"漂移"）。

```json
{
  "engineInstanceId": "hd-engine-01",
  "surveyId": 700123,
  "expectedFingerprint": "fm1:74e0d199d9839cdc",
  "binding": { "…": "可选：平台存档的绑定记录原文（BindingRecord.to_dict()）" }
}
```

- 前三个字段必填，`binding` 可选，不得有其他字段。`expectedFingerprint` 形如 `fm1:<hex>`。
- 带 `binding` 时它必须正是所指的那份：`engineInstance`、`surveyId`、`fingerprint` 与请求一致，否则 400。
  带上它能精确报出"哪道题的代码被改成了什么"；不带时只比指纹。
- 网关只调用 `get_fieldmap` 与 `get_survey_properties`，**绝不改写引擎**。

| 状态 | 体 | 含义 |
|---|---|---|
| 200 | `{"status":"match","result":<DriftResult>}` | 结构与期望一致，问卷仍处于激活状态 |
| 200 | `{"status":"drift","result":<DriftResult>}` | 发现漂移，`issues` 说明原因 |
| 502 | `{"status":"failed","error":"engine_error","detail":"…"}` | 引擎读不出来（权限、传输等），结论未知 |
| 400 / 401 / 404 / 500 | 同 v1 | |

`<DriftResult>`：

```json
{
  "surveyId": 700123,
  "expectedFingerprint": "fm1:74e0d199d9839cdc",
  "currentFingerprint": "fm1:0e1235f2e2bc5a95",
  "drifted": true,
  "active": "Y",
  "renamed": [{"uuid": "33333333-…", "from": "QSINGLE", "to": "QDRIFTED"}],
  "issues": [{"code": "E_CODE_DRIFT", "detail": "…"}]
}
```

`issues[].code` 取值：`E_CODE_DRIFT`、`E_FIELD_DISAPPEARED`、`E_FIELD_ADDED`、`E_FINGERPRINT_DRIFT`、
`E_FINGERPRINT_VERSION`、`E_SURVEY_EMPTY`（沿用 ADR 0009 决定 7），以及本版新增的
`E_SURVEY_MISSING`（sid 已不存在，此时 `currentFingerprint` 与 `active` 为 null）和
`E_SURVEY_NOT_ACTIVE`（被停用）。**过期不算漂移**：那是 `POST /v1/close` 的正常结果。

已知盲区（ADR 0009 已知限制 1、8）：答案选项代码与题干文案不进指纹，改了也报 `match`。

## 平台侧必须做到

- 只对**已被取代、路由已切走**的版本调用 `/v1/close`；绝不收口当前在线的版本。
- 漂移检查的结论只做记录与告警，**绝不自动重新发布或覆盖**引擎里的改动。
