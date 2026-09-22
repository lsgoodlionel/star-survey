# ADR 0014：企业微信 / 钉钉 / 飞书组织授权与免登

- 状态：已决定（待用户确认第 3 条默认值）
- 日期：2026-09-22
- 关联：WP-20.1（R20-01～R20-03、R20-04 的重放 / 离职 / 登出部分）、WP-18.1（R18-01 离职停权、R18-08 openid 作用域）、ADR 0010

## 决定

1. **组织连接按租户配置**（`org_connection`，V105，行级安全）：提供方、组织标识 `corpId`（企业微信 corpid / 钉钉 corpId / 飞书
   tenant_key）、应用标识 `appId`（agentid / AppKey / App ID）、**密钥名** `secretRef`、未知成员策略、启用状态。
   建与改需要租户级 `manage-settings`，逐次审计。数据库、响应、审计、日志里只有密钥名，没有密钥值。
2. **密钥按"租户 + 名字"解析**：`PLATFORM_ORG_SECRET_<租户标识去横线大写>_<密钥名>`，由环境或密钥库注入（`SecretResolver`
   可换成 Vault 等实现）。租户无论填什么名字都只能解析到自己命名空间里的密钥，不能借用别的租户的应用凭据。
3. **未知成员默认拒绝**（`require_preauthorized`）：组织里平台不认识的人来免登，返回 403，须管理员先预授权
   （`POST /v1/org-connections/{id}/authorized-users`，`manage-members`：绑定身份并按员工邀请、占席位，首次登录时邀请生效）。
   连接可改为 `jit_staff`：首次登录即开通为员工、占一个席位、**不授予任何角色**；没有空闲席位时拒绝（403 `seat_limit`）。
   理由：企业通讯录往往远大于购买的席位，默认 JIT 会让任何在通讯录里的人自动占席位、进入租户；默认拒绝是更安全的一侧。
   被管理员移除或离职撤销的人，再次免登不会被 JIT 自动恢复。
4. **身份 = (提供方, 组织, 组织级用户标识)**，按租户绑定（沿用 V104）：企业微信 userid、钉钉 userid（经 unionId 换取）、
   飞书 user_id（不用 open_id，后者只在一个应用内唯一）。同一组织下不同应用是同一个人；不同组织下相同的字符串是不同的人，
   不合并；同一个人在两个租户是两个互不相关的主体。
5. **流程**：`start` 生成 state（32 字节随机十六进制，满足企业微信"字母数字 ≤128 字节"）与浏览器随机值，state 入库绑定租户、
   连接、随机值摘要，5 分钟有效，随机值进 HttpOnly + SameSite=Lax + Secure Cookie；`callback` **先原子地烧掉 state** 再核对
   连接、Cookie、有效期，之后才在服务端兑换授权码。重放、过期、换租户（行级安全下不可见）、换浏览器（登录 CSRF）统一
   400 `invalid_login_state`，不给区分线索。钉钉 `corpId`、飞书 `tenant_key` 与连接不符时 403 `wrong_organisation`。
   这几家的授权码流程不返回 id_token，因此 OIDC nonce 的作用由浏览器绑定随机值承担。
6. **令牌签发**：平台此前只验签不签发，本次新增 `PlatformTokenIssuer`，与 ADR 0010 一致用同一 HMAC 密钥签 HS256；
   声明 `sub`=主体、`tenant_id`、`sid`=会话、`amr`=["org:<提供方>"]、`roles`=[]（权限一律由 access 模块判定）。
   接入外部身份服务后改为由其签发、按 JWK 验签，本类退役。
7. **撤销策略：短时令牌 + 每请求会话检查**。令牌默认 10 分钟；每次登录一行 `org_login_session`（V106）。主链 `JwtDecoder`
   新增可插拔校验器（`SecurityConfig` 的唯一改动），身份模块的 `OrgSessionTokenValidator` 对带 `sid` 的令牌在其租户内核对：
   会话未撤销、未过期、绑定未撤销。离职、登出在**下一个请求**即 401，不等令牌过期。核对出错失败即关闭。
   不带 `sid` 的令牌（现有测试令牌、未来外部签发方）不受影响。
8. **离职后立即失权**：绑定表不可改写（V103），撤销单独记在只追加的 `identity_binding_revocation`（V107）。撤权 = 记撤销 +
   撤销全部会话 + 移除成员（删授权、释放席位；租户唯一在职所有者只撤会话不移除，记告警）。入口两个：管理员触发的拉取同步
   `POST /v1/org-connections/{id}/sync`（开放平台明确答复"离职 / 禁用 / 不存在"才撤，查询失败计入 unknown、不撤），以及供
   通讯录事件回调调用的 `OrgDirectorySyncService.memberDeparted`。

## 取舍

- 每请求一次会话查询换来即时撤权；只对免登令牌生效，成本可控。若以后请求量大，可换成撤销列表缓存 + 更短 TTL。
- state 失败统一一个错误码，排查靠审计（`identity.org_login.denied`，含原因）而不是响应。
- 查询失败不撤权，意味着开放平台长时间故障期间离职者要等到恢复后的下一次同步；这是为了不因一次故障全员下线。

## 真实接入需要的配置

| 项 | 企业微信 | 钉钉 | 飞书 |
|---|---|---|---|
| corpId | 企业 ID（corpid） | 组织 corpId | 租户 tenant_key |
| appId | 自建应用 AgentId | 应用 AppKey（Client ID） | 应用 App ID（cli_…） |
| 密钥（环境变量值） | 应用 Secret | AppSecret（Client Secret） | App Secret |
| 开放平台侧登记 | 网页授权及 JS-SDK 可信域名 + 企业微信授权登录回调域 | 登录与分享 → 回调域名 | 安全设置 → 重定向 URL |
| 应用权限 | 通讯录读取成员（同步用） | 通讯录个人信息读、成员信息读 | 获取用户 user ID、读取通讯录用户状态 |

平台侧：`PLATFORM_PUBLIC_BASE_URL`（回调地址 = 它 + `/v1/auth/org/{租户}/{连接}/callback`，连接的 `redirectUri` 字段即此值），
每个连接一个 `PLATFORM_ORG_SECRET_<租户>_<密钥名>`。开放平台地址默认即正式环境，无需配置。

## 依据的官方文档

本次实际查阅并据以实现的：企业微信 91022、91023、91039；钉钉"登录第三方网站"；飞书获取授权码、user_access_token、用户信息。
其余链接（企业微信 90196 / 98152、钉钉各分页、飞书 tenant_access_token 与通讯录用户）按接口名列出、未逐页核对，
字段取自接口惯例（如企业微信 user/get 的 status、钉钉 60121、飞书 status.is_resigned / is_frozen），真实接入前须逐项核对。

- 企业微信：网页授权 https://developer.work.weixin.qq.com/document/path/91022 ；获取访问用户身份
  https://developer.work.weixin.qq.com/document/path/91023 ；获取 access_token https://developer.work.weixin.qq.com/document/path/91039 ；
  读取成员 https://developer.work.weixin.qq.com/document/path/90196 ；Web 登录组件（扫码）https://developer.work.weixin.qq.com/document/path/98152
- 钉钉：登录第三方网站 https://open.dingtalk.com/document/orgapp/tutorial-obtaining-user-personal-information ；获取用户 token
  https://open.dingtalk.com/document/orgapp/obtain-user-token ；获取企业内部应用 accessToken
  https://open.dingtalk.com/document/orgapp/obtain-the-access_token-of-an-internal-app ；根据 unionid 获取 userid
  https://open.dingtalk.com/document/orgapp/query-a-user-by-the-union-id ；查询用户详情 https://open.dingtalk.com/document/orgapp/query-user-details
- 飞书：获取授权码 https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code ；获取 user_access_token
  https://open.feishu.cn/document/authentication-management/access-token/get-user-access-token ；获取用户信息
  https://open.feishu.cn/document/server-docs/authentication-management/login-state-management/get ；tenant_access_token
  https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal ；获取单个用户
  https://open.feishu.cn/document/server-docs/contact-v3/user/get

## 未做（后续）

- 通讯录事件回调端点（企业微信加密 XML、钉钉 / 飞书事件订阅的验签解密）；定时拉取调度与分页批处理；部门与标签映射（R18-02）。
- 复职：撤销只追加，重新启用需单独设计的管理员恢复流程。
- `start` 为匿名端点，需在网关层限流；过期 state 在下次发起时清理（保留 1 天）。
- 租户停用（suspended）时拒绝免登需要租户模块提供状态查询契约。
- 企业微信"扫码登录"`mode=qr` 只生成了登录页地址，未在替身里单独覆盖。
- 飞书 v2 token 接口已提示迁移到 accounts.feishu.cn/oauth/v3/token，真实接入前复核。
