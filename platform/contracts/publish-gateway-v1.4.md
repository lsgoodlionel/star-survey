# 契约增补：平台 → 发布网关（v1.4，2026-09-28）

本文件是 [publish-gateway-v1.md](publish-gateway-v1.md) 的**向后兼容增补**，补上
[ADR 0016](../docs/adr/0016-access-policy.md) 记着的缺口「邀请码撤销的平台接口（引擎侧删参与者即时生效）」，
决定见 [ADR 0021](../docs/adr/0021-engine-write-paths.md)。

- `POST /v1/publish`、`POST /v1/close`、`POST /v1/drift-check`、`GET /healthz` 的行为**一字不改**；
  v1.1 的澄清、v1.2 的增补、v1.3 的留存期全部继续有效。
- 新增一个路径，部署、信任边界、HMAC 认证（同一密钥、同样的请求头与 ±300 秒）、1 MiB 请求体上限、
  `Content-Type: application/json`、口令脱敏（`***`）与单副本限制都与 `/v1/publish` 相同。
- 为什么不升 v2：没有任何既有字段的含义变化，只多了一个路径。

## `POST /v1/participants/revoke`

让一个邀请码**立刻**打不开问卷：删掉引擎 `tokens_<sid>` 里那一行参与者。

```json
{
  "requestId": "9f2c…",
  "engineInstanceId": "hd-engine-01",
  "surveyId": 700123,
  "participantToken": "a1b2c3d4e5f6g7h8"
}
```

- 四个字段缺一不可、不得多出。`requestId` 为规范 UUID，**只用于日志关联**；`surveyId` 为正整数；
  `participantToken` 必须匹配 `[A-Za-z0-9_-]{4,64}`（与平台 `contact_participation.participant_token`
  的 CHECK 约束一字不差）。
- **实例与 sid 由平台按自己那条映射给出**，不是"当前在线版本"：按 [ADR 0012](../docs/adr/0012-republish-and-drift.md)，
  新版本是引擎里另一份问卷、另一套参与者表，令牌活在签发它的那一份上。旧版本的码要在旧 sid 上撤。
- 网关做两步：`get_participant_properties` 按 token 查出 `tid`，再 `delete_participants` 删掉。
  删完**再查一次**确认那一行真的没了。
- 为什么是删、不是停用：引擎的作答入口按参与者表里有没有这一行放人，行没了就**立刻**进不去，
  不用等任何缓存或会话过期。`usesleft` / `validuntil` 也能拦人，但它们的语义是"用完了 / 过期了"，
  而且 `validuntil` 与问卷时区、引擎时钟纠缠在一起——撤销不该依赖时钟。
- 天然幂等：引擎里已经没有这个码时同样返回 200（`alreadyAbsent` 为真）。因此**不进 requestId 结果存档**；
  同一 `(实例, sid, token)` 同一时刻只处理一个请求。这把锁与 v1.1 说的一样是**进程内**的，
  多副本下并发的第二个请求会拿到 502 而不是 409——结局仍然安全（第二次删拿不到 `Deleted`，
  平台据此不写 `revoked_at`），重试即收敛。
- 答卷不受影响：删的是参与者行，已经收到的答卷、答卷表与结构原样保留。

| 状态 | 体 | 含义 |
|---|---|---|
| 200 | `{"status":"revoked","result":{"surveyId":700123,"tokenId":42,"alreadyAbsent":false}}` | 那一行已被删掉，回读确认不在了 |
| 200 | `{"status":"revoked","result":{"surveyId":700123,"tokenId":null,"alreadyAbsent":true}}` | 调用前引擎里就没有这个码（幂等重试，或已有人在引擎侧删过）。结论同样是"进不去" |
| 502 | `{"status":"failed","error":"E_SURVEY_MISSING","detail":"…"}` | 引擎里没有这个 sid |
| 502 | `{"status":"failed","error":"E_NO_PARTICIPANT_TABLE","detail":"…"}` | 这份问卷没有参与者表 |
| 502 | `{"status":"failed","error":"E_REVOKE_NOT_APPLIED","detail":"…"}` | 引擎没删掉（权限、并发），或删完回读那一行还在 |
| 502 | `{"status":"failed","error":"engine_error","detail":"…"}` | 其他引擎侧失败（登录被拒、传输错误等），`detail` 已脱敏 |
| 409 | `{"status":"conflict","error":"revoke_in_progress"}` | 同一个码的撤销正在进行 |
| 400 / 401 / 404 / 500 | 同 v1 | 请求体不合法 / 认证失败 / 未知实例 / 网关意外异常 |

### 为什么"问卷不存在"是失败而不是"已撤销"

看上去问卷都没了，那个码当然没用。但**实例 id 配错**也长这个样子——那时真正的令牌还在另一台
引擎上活着。把它报成成功，平台就会写下 `revoked_at`，于是又回到这个缺口的本体：
「平台以为撤销了，令牌照样能开问卷」，而且**没人会再发现**。

代价是：一份在引擎里被人工删掉的问卷，它的映射会一直撤不掉，需要运维处置。这是刻意选的方向——
撤销失败看得见，撤销假成功看不见。

## 平台侧必须做到

- **先引擎、后平台**：只有拿到 200 才写 `revoked_at`；任何非 200（含超时、网关未配置）都当成
  "**没有**撤销"，不写库，并让调用方看到失败（平台对外 503 `revocation_not_applied`）。
- 邀请码是能直接进入问卷的凭据：**不进日志、不进审计、不进错误消息**。本接口的请求体里有它，
  两端都不得把请求体写进日志。
