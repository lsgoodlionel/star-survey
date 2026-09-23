# 问卷访问策略 v1（`policy` 块，policyVersion 1）

WP-04 切片 04.1／04.2。设计与取舍见 [ADR 0016](../docs/adr/0016-access-policy.md)。
实现：平台 `SurveyAccessPolicies`（草稿保存）→ 网关 `pubgw/policy/`（校验、编译、回读）→
插件 `plugins/MjyRuntimePolicy`（执行）。实现与本文件不一致时以测试为准并修正本文件。

## 1. 位置与版本

定义（`definitionVersion` 1 或 2）的可选顶层键 `policy`。不带 `policy` 的定义编译结果逐字节不变。

```json
"policy": {
  "policyVersion": 1,
  "window":  {"opensAt": "2026-10-01T09:00", "closesAt": "2026-10-07T18:30", "timezone": "Asia/Shanghai"},
  "access":  {"passwordHash": "pbkdf2-sha256$600000$<salt>$<hash>", "captcha": true, "invitationRequired": true},
  "limits":  {"responses": [{"by": "token", "max": 1}, {"by": "ip", "max": 20}], "maxDurationSeconds": 1800},
  "network": {"allowIps": ["10.0.0.0/8"], "denyIps": ["10.9.9.9"], "allowRegions": ["CN"], "denyRegions": [],
              "regionUnknown": "deny"}
}
```

## 2. 字段

| 字段 | 取值 | 执行者 | 说明 |
|---|---|---|---|
| `policyVersion` | `1` | — | 必填 |
| `window.opensAt` / `closesAt` | `YYYY-MM-DDTHH:MM[:SS]`，不带偏移 | 原生＋插件 | 至少一个；区间 `[opensAt, closesAt)`；夏令时不存在或重叠的时刻拒绝 |
| `window.timezone` | IANA 名 | — | 平台缺省填 `Asia/Shanghai` |
| `access.password` | 明文 | — | **只在平台草稿接口出现**，保存时换成 `passwordHash`；到达网关即 422 |
| `access.passwordHash` | `pbkdf2-sha256$迭代$salt$hash`（base64，salt ≥ 8 字节，hash 32 字节，迭代 10 万–1000 万） | 插件 | 平台生成 60 万次 |
| `access.captcha` | 布尔 | 原生 `usecaptcha=X` | 与 `settings.usecaptcha` 互斥 |
| `access.invitationRequired` | 布尔 | 原生 `access_mode=C` | 需要 `participants`；与 `settings.access_mode` 互斥 |
| `limits.responses[]` | `{by: token|device|ip, max: 1–10000}` | 插件 | 每个维度最多一条；`token` 需要 `invitationRequired` |
| `limits.maxDurationSeconds` | 60–604800 | 插件 | 从本次作答首次进场起算，服务端时钟 |
| `network.allowIps` / `denyIps` | IPv4／IPv6 地址或 CIDR | 插件 | deny 优先；allow 非空时不在其中即拒绝 |
| `network.allowRegions` / `denyRegions` | ISO 3166（`CN`、`CN-BJ`） | 插件 | `CN` 覆盖 `CN-BJ` |
| `network.regionUnknown` | `deny`（缺省）／`allow` | 插件 | 查不到地区时的处理 |

`window` 与 `settings.startdate` / `settings.expires` 互斥。任何未知键、类型错误、越界都是
422 `validate`，问题代码见 §3。

## 3. 问题代码（422 `validate`）

`E_POLICY_INVALID` `E_POLICY_VERSION` `E_POLICY_EMPTY` `E_POLICY_UNKNOWN_KEY` `E_POLICY_TYPE`
`E_POLICY_RANGE` `E_POLICY_TIMEZONE` `E_POLICY_DATETIME` `E_POLICY_LOCAL_TIME` `E_POLICY_WINDOW_EMPTY`
`E_POLICY_WINDOW_ORDER` `E_POLICY_PLAINTEXT_PASSWORD` `E_POLICY_PASSWORD_HASH` `E_POLICY_INVITATION`
`E_POLICY_IDENTITY` `E_POLICY_CIDR` `E_POLICY_REGION` `E_POLICY_SETTING_CONFLICT`

## 4. 插件载荷与回读

需要插件执行的规则编译成一行 LSS `plugin_settings`（`name=MjyRuntimePolicy`、`key=mjy_access_policy`），
值是键排序、紧凑、纯 ASCII 的 JSON：

```json
{"maxDurationSeconds":1800,"network":{...},"passwordHash":"...","responses":[{"by":"token","max":1}],
 "schema":"mjy-access-policy/1",
 "window":{"closesAt":"2026-10-07 10:30:00","closesAtLocal":"2026-10-07T18:30","opensAt":"...","opensAtLocal":"...","timezone":"Asia/Shanghai"}}
```

`responses` 按 token → device → ip 排序；时间是 UTC、引擎格式。只有验证码／邀请码时没有载荷。

发布 `apply` 阶段（激活前）网关 `GET <engine>/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=policyStatus&sid=<sid>`，
期望 `{"plugin":"MjyRuntimePolicy","active":true,"surveyId":<sid>,"rows":1,"valid":true,"policyDigest":<载荷 SHA-256>}`。
不符 → `apply` 失败并回滚：`E_POLICY_NOT_ENFORCED`（插件未激活／未应答／份数不为 1）、
`E_POLICY_REJECTED`（插件解析不了）、`E_POLICY_DIGEST_MISMATCH`、`E_POLICY_UNVERIFIED`（没有回读通道）。

## 5. 网关应答

`POST /v1/publish` 成功且带插件策略时，`result` 多一个键 `policyDigest`（64 位十六进制）；
没有插件策略时应答与契约 v1 完全一致。

**平台必须核对这个键**（ADR 0016 决定 5）：把自己发出去的那份策略按 §4 重新编译、算 SHA-256，
与回执逐字比对。定义需要插件却没有摘要、摘要不一致、或定义不需要插件却带了摘要，都判发布失败
（不登记路由、不写已发布版本）。旧网关会忽略不认识的 `policy` 块，这一条正是为了挡住它。
两端的规范化规则由同一张向量表钉住：`platform/services/business/src/test/resources/policy/digest-vectors.json`，
网关 `tests/test_policy_digest_vectors.py` 与平台 `AccessPolicyDigestTest` 各读一次。
