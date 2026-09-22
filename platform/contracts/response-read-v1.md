# 契约：平台 → 发布网关 答卷读取（v1）

平台业务服务（`cn.mjy.platform.response.HttpResponseAnswerSource`）按页向网关（`pubgw/responses.py`）读取答卷作答值。
设计与取舍见 ADR 0013。改动须同步两端并升版本。

## 部署与信任边界

与 `publish-gateway-v1.md` 完全相同：同一网关进程、同一共享密钥（平台 `PLATFORM_PUBGW_SECRET` / 网关 `PUBGW_SHARED_SECRET`）、
同一签名规则（`X-Pubgw-Timestamp`、`X-Pubgw-Signature = hex(HMAC-SHA256(secret, "<timestamp>.<原始请求体>"))`，±300 秒）。
**引擎口令只在网关。** 网关不做业务授权：平台必须先完成权限判定，且只传本租户已发布版本里记录的 `(engineInstanceId, surveyId)`。

## `POST /v1/responses/read`

请求体（`Content-Type: application/json`，≤ 1 MiB），恰好四个字段：

```json
{
  "engineInstanceId": "hd-engine-01",
  "surveyId": 900001,
  "responseIds": [1, 3, 999],
  "fields": ["Q1", "Q1_Cother", "Q5_S8#0"]
}
```

- `surveyId`：引擎 sid，正整数。
- `responseIds`：1–500 个互不相同的正整数。
- `fields`：1–5000 个互不相同的引擎答卷列名（`[A-Za-z0-9_#]{1,64}`），不得包含 `id`。

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
| 502 | `{"error":"engine_error"}` | 引擎登录被拒、RPC 失败、导出结果不是 base64 JSON 或列数不符；不带引擎错误原文 |
| 500 | `{"error":"internal_error"}` | 网关意外异常，不含堆栈 |

`GET /v1/responses/read` → 405（`Allow: POST`）。响应头同发布端点：`Cache-Control: no-store`。

## 平台侧必须做到

- 只在 `view-raw-responses` 判定通过后调用；敏感列遮蔽在平台侧完成（网关返回原值）。
- 只为投影状态非删除、且属于该 sid 最近代次的答卷调用（ADR 0013 决定 3）。
- 应答里出现未请求的答卷号、重复答卷号或形状不对，一律视为不可信（503），不返回部分结果。
- 非 200 一律视为"暂时取不到作答"（503），不重试写操作——本端点只读、天然幂等，调用方可直接重试。
- 请求与应答正文含个人数据，两端都不记录正文。

## 验证

- 网关单测：`platform/tools/publish-gateway/tests/test_responses.py`（假引擎按 7.1.2 导出形状作答）。
- 真引擎：`TEST_DB=mysql|pgsql platform/deploy/test/run-response-read.sh`（多列题、"其他"、评论、双尺度逐列核对）。
- 平台：`HttpResponseAnswerSourceTest`（签名逐字节、应答解析、失败即关闭）。
