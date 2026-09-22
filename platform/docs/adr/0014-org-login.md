# ADR 0014：企业微信 / 钉钉 / 飞书组织授权与免登

- 状态：已接受（2026-09-22 用户确认第 3 条默认值：未授权成员默认拒绝，需管理员预授权；每个连接可改为即时开通）
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

## 增补一（第三波）：身份绑定接口的权限

`/v1/identity-bindings`（P1 起没有权限判定）收紧如下，判定在 `IdentityBindingService` 的带上下文方法里，由 access 模块按成员与授权数据判定（令牌 `roles` 声明不参与）：

| 操作 | 要求 |
|---|---|
| `POST /v1/identity-bindings` 建绑定 | 租户级 `manage-members`（只在项目上有该权限的项目管理者不算） |
| `GET /v1/identity-bindings?provider&appId&externalId` 按身份反查 | 租户级 `manage-members`（反查即枚举，不对本人开放） |
| `GET /v1/identity-bindings/{principalId}` | 本人，或租户级 `manage-members` |

- **"本人"的判定**：已验签令牌的 `sub` 与路径中主体标识的规范（小写）字符串逐字相等。组织免登签发的令牌 `sub` 即主体标识，且带 `sid`、每个请求另有会话撤销检查；外部签发方若也以主体标识作 `sub` 则同样适用。不做大小写归一，避免同一主体的多种写法。
- 判定先于查询：无权者对存在与不存在的主体一律 403，不给出存在性线索；跨租户仍由行级安全表现为 404。
- 只带租户标识的服务方法保留给系统流程（免登、同步、预授权），不做判定，由调用方负责。
- `GET /v1/me` 只回显调用者自己令牌里的租户、主体与声明，不读任何租户数据，不需要额外判定；组织连接各端点此前已按 `manage-settings` / `manage-members` 判定。

## 增补二（第三波）：通讯录事件回调、分页 / 定时同步、恢复、停用租户

### 事件回调

- 端点：`GET|POST /v1/org-events/{租户}/{连接}`，独立过滤链（顺序 3，匿名、不认 Authorization、无 CSRF），开放平台由连接决定。
  连接新增两个**密钥名**（V109）：`eventTokenRef`（企业微信 Token / 钉钉 token / 飞书 Verification Token）与
  `eventKeyRef`（企业微信 EncodingAESKey / 钉钉 aes_key / 飞书 Encrypt Key），须同时配置，按 `PLATFORM_ORG_SECRET_<租户>_<名字>` 解析；
  未配置的连接对该端点 404。连接视图给出须在开放平台登记的 `eventUrl`。
- 请求体上限 `platform.identity.org-events.max-body-bytes`（默认 64 KiB，超限 413、不解析）；时间戳窗口 `max-clock-skew`（默认 5 分钟）。
- **验签失败、时间戳越界、无法解密、接收方 / 应用 / 组织 / 令牌不符一律 401，且在任何写入之前**；XML 解析禁用 DTD 与外部实体（XXE → 400）。

| | 企业微信 | 钉钉 | 飞书 |
|---|---|---|---|
| 请求 | `GET ?msg_signature&timestamp&nonce&echostr`（URL 校验）；`POST` 同参数 + XML `<Encrypt>` | `POST ?msg_signature(signature)&timeStamp(timestamp，毫秒)&nonce` + `{"encrypt"}` | `POST {"encrypt"}` + `X-Lark-Request-Timestamp/Nonce`、`X-Lark-Signature` |
| 签名 | SHA-1(字典序拼接 token、timestamp、nonce、密文) | 同左 | SHA-256(timestamp + nonce + Encrypt Key + 原始体) |
| 解密 | AES-256-CBC，key = Base64(EncodingAESKey+"=")，IV = key 前 16 字节，PKCS#7 补位到 32 字节；明文 = 16B 随机 + 4B 长度 + 消息 + receiveid | 同左；receiveid = 应用 AppKey（或旧接口的 corpId） | key = SHA-256(Encrypt Key)，IV = 密文前 16 字节，AES-256-CBC + PKCS#7 |
| 额外核对 | receiveid 与内层 ToUserName = corpid | 内层 CorpId = 连接 corpId | header.token = Verification Token，header.app_id = App ID，header.tenant_key = tenant_key |
| 握手 | 返回解密后的 echostr 明文 | `check_url` → 加密签名的 "success" | `url_verification`（可不带签名头，但须能用 Encrypt Key 解开且 token 相符）→ `{"challenge"}` |
| 离职 | `change_contact` 的 `delete_user`，或 `update_user` 且 `Status` ∈ {2 禁用, 5 退出} | `user_leave_org` 的 `UserId[]` | `contact.user.deleted_v3`，或 `updated_v3` 且 `status.is_resigned / is_frozen`；取 `object.user_id` |
| 去重键 | 解密后整条消息的 SHA-256 | 同左 | `header.event_id` 的 SHA-256 |

- 去重与副作用：已有回执（`org_event_receipt`，V109，行级安全、复合外键、只追加）则只应答；否则逐个调用
  `OrgDirectorySyncService.memberDeparted`（撤绑定、撤会话、移出成员），**成功后**才写回执——处理失败时开放平台重试会重新处理，撤权本身幂等。
- 飞书只接受加密体（连接必须配置 Encrypt Key），不接受明文推送。飞书 1.0 结构事件只核对令牌后确认，不处理。

### 分页与定时同步

- 拉取同步按 `(created_at, principal_id)` 键集分页（`platform.identity.org-sync.batch-size`，默认 200），管理员触发与定时共用；
  判定规则不变：只有开放平台明确答复"离职 / 禁用 / 不存在"才撤，查询失败计入 unknown。
- 定时全量同步 `OrgDirectorySyncScheduler`：`platform.identity.org-sync.enabled`（**默认关闭，测试关闭**）、`interval` 默认 6 小时。
  每轮逐个未关闭租户（`TenantDirectory.openTenants`）的**启用**连接；单个连接失败（密钥缺失、平台不可用）只记日志。
- 未使用开放平台的"部门成员列表"全量接口：逐个核对已绑定的人即可发现离职，且不会因列表接口分页 / 权限问题误判"不存在"。

### 恢复（复职）

- `POST /v1/identity-bindings/{principalId}/reinstatement`，租户级 `manage-members`，审计 `identity.binding.reinstate`。
  先按员工重新邀请（占席位；无空闲席位 409 且什么都不改），再在撤销上盖恢复戳；此人下次免登时邀请生效。旧会话不复活。
- V110：撤销行改为自有 `id`，同一主体可多次"撤销 → 恢复 → 再撤销"；运行期账号只能改 `reinstated_at / reinstated_by` 两列，
  触发器保证只能由空改为非空、只改一次、其余列不可变；部分唯一索引保证同一主体同时至多一条未恢复的撤销。
  "已撤销" = 存在未恢复的撤销（会话检查、免登、同步、预授权同一口径）。未撤销 409，别的租户 404。

### 停用租户

- `shared` 的 `TenantDirectory` 新增 `isActive(TenantId)`（只给布尔结论）。免登 `start` 与 `callback` 在任何状态变更之前核对：
  非 active（开通中、停用、关闭）一律 403 `tenant_unavailable`，state 不被消耗。已签发的免登令牌不在此列（见"仍未做"）。

### 依据的官方文档（本次查阅）

- 企业微信：加解密方案 https://developer.work.weixin.qq.com/document/path/90968 ；成员变更事件
  https://developer.work.weixin.qq.com/document/path/90970 ；官方 PHP 库示例向量 https://github.com/sbzhu/weworkapi_php （callback/Sample.php）
- 钉钉：官方加解密库与示例 https://github.com/open-dingtalk/DingTalk-Callback-Crypto （含 DingCallbackCrypto3.py 的解密向量）；
  事件订阅 https://open.dingtalk.com/document/development/event-subscription-and-data-push ；通讯录事件
  https://open.dingtalk.com/document/orgapp/address-book-events （后两页为前端渲染，未能抓取正文；`user_leave_org` 的
  `UserId` / `CorpId` 字段按官方示例与接口惯例，真实接入前须核对）
- 飞书：将事件发送至开发者服务器（url_verification）
  https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/choose-a-subscription-mode/send-notifications-to-developers-server ；
  Encrypt Key 解密与签名（含 "test key" 向量）
  https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case ；
  员工离职 https://open.feishu.cn/document/server-docs/contact-v3/user/events/deleted ；员工信息变化
  https://open.feishu.cn/document/server-docs/contact-v3/user/events/updated
- 三份官方向量（企业微信 URL 校验与消息、钉钉 check_url、飞书 "hello world"）都在 `OrgEventCryptoTest` 里逐字验证通过。

### 仍未做

- 停用租户已签发的免登令牌仍可用到过期（≤10 分钟）；平台级"停用租户拒绝一切请求"应在主链统一做，不只在身份模块。
- 事件回执没有保留期清理（只追加）；量大时需要按 `received_at` 归档。
- 部门与标签映射（R18-02）；钉钉 Stream 模式、飞书长连接模式未接；`start` 与事件端点的网关限流。
- 企业微信"成员变更"之外的通讯录事件（部门删除等）与飞书 `contact.user.created_v3` 只确认、不处理。
