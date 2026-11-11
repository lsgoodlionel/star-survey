# Survey 单机生产部署

本目录是 Ubuntu 22.04/24.04、AMD64/ARM64 的 Docker Compose 单机生产运行时。生产安装只消费
Release manifest、校验过的架构 bundle 和固定 digest 镜像；服务器不需要源码，也不会在镜像拉取失败时回退到本地构建。

## 前提

- systemd 正常运行，Docker Engine >= 24，Docker Compose plugin >= 2.20。
- 至少 4 GiB 内存、目标文件系统至少 20 GiB 可用空间。
- 公网 DNS 已指向本机或显式声明的 NAT 地址，80/443 可用，系统时间已同步。
- Release manifest、bundle 和镜像必须来自同一发布版本。在线安装必须提供 manifest SHA-256；离线包必须完成同等校验。
- 管理员密码和所有服务密钥由安装过程生成在 `<target>/shared/secrets`，目录权限 `0700`、文件权限 `0600`。不要把该目录写入日志、工单或仓库。
- 文件型 Compose secret 保留宿主文件权限，不能依赖 Compose 的 `uid/gid/mode` 转换。安装器将需要挂载的密钥同步到 `<target>/shared/runtime-secrets`（目录 `0700`，副本 `0444`），供各服务按既有 secret 列表只读挂载。宿主上的 `shared` 和副本目录均禁止其他用户遍历；原始密钥保持 `0600`，`backup_encryption_key` 不生成挂载副本。派生管理员密码哈希生成后会同步副本；安装及升级会从原始文件刷新副本，不要直接编辑副本。
- 网关需要的生成配置 `engines.json` 为 `0644`，所在 release 目录保持 `0700`。这些权限用于让非 root 容器读取单文件挂载，不应放宽宿主目录权限。
- 组织登录的管理端交接地址使用与 `PUBLIC_HOST` 相同的 HTTPS 根地址；实际组织连接、成员授权和供应商凭据仍需另行配置并验证。

## 安装目录信任边界

安装根目录、其 `releases/`、版本目录和 helper 必须由运行 surveyctl 的有效用户拥有，且禁止组或其他用户写入（不得有 `0022` 权限位）。预建空目标也受相同限制；控制器拒绝不安全目录，不会自动 chmod/chown 修复已有现场。以 root 执行时，不接受普通用户拥有的安装树或可替换安装树的祖先。目标之外的祖先只允许 root 或执行用户拥有且不可被其他用户写入；root 拥有的 sticky 临时祖先（例如 `/tmp`）可容纳私有目标，目标本身不享有此例外。

只读共享配置/挂载所需的 `0755` 目录、`0644`/`0444` 文件仍可接受。backup、verify 和 doctor 启动安装包内 helper 前，复核安装路径权限及 bundle/生成配置完整性；在锁定后也复核目标目录身份。异常保留操作标记和现场，须由受控运维核实，不应删除标记或放宽权限强行继续。

这些检查约束不同用户通过可写路径替换 helper 的风险，不隔离同一 uid、root、宿主或 Docker 管理员，也不使旧 CLI 自动遵守新控制器的锁。入口 surveyctl 本身必须由部署管理员从已校验的可信制品启动。

## 可选 SMTP Email 投递

基础 `compose.yml` 默认以 `PLATFORM_DELIVERY_ENABLED=false` 停止后台批量投递 worker。管理员可在管理端的“邮件服务设置”页按租户保存供应商、发件邮箱和授权凭据；读取页面只显示脱敏状态，凭据不会回显。保存配置后可在同页填写收件地址、主题和纯文本正文，明确点击“发送测试邮件”进行一次真实 SMTP 提交；“仅验证登录”只建立并认证 SMTP 会话，不发送邮件。此能力要求部署侧提供权限为 `0600`、长度至少 32 字节的 `PLATFORM_DELIVERY_SECRET_FILE`：平台按租户派生 AES-GCM 密钥加密 SMTP 凭据，缺失时保存和读取已保存凭据都会失败关闭，绝不降级为明文或浏览器持久化。至少一个租户完成登录验证和测试发送后，运维人员才可将 `PLATFORM_DELIVERY_ENABLED=true` 并重启 platform 服务，启用批量任务消费。

管理员设置页面仅供具备 `MANAGE_SETTINGS` 权限的租户成员使用。页面的“验证连接”只完成 SMTP 登录与 TLS 协商，不发送邮件；保存、清除和验证均会记录脱敏审计信息。部署环境、Docker Compose 与本地启动使用同一 API 和加密数据模型，差异仅在 `PLATFORM_DELIVERY_SECRET` 的注入来源（Docker Secret、Kubernetes Secret 或本地受限环境变量）。

仍可使用启动时的可选 SMTP 覆盖层作为迁移回退；它不应与同租户的管理端配置混用。要启用该回退，先用隔离的、可撤销的测试邮箱完成供应商侧的 TLS 与授权验证，然后以可选覆盖层启动：

```bash
docker compose -f compose.yml -f compose.email.yml up -d
```

只支持经固定配置档核对的 `gmail`、`outlook-com`（Hotmail/Outlook.com）与 `netease-163`：Gmail 显式设 `PLATFORM_DELIVERY_EMAIL_AUTH_MODE=app-password` 或 `oauth2`；网易 163 先在网页邮箱开启 SMTP 并设为 `app-password`；Outlook.com/Hotmail 必须设为 `oauth2` 并使用 OAuth2 access token，不能使用网页登录密码。`PLATFORM_DELIVERY_EMAIL_CREDENTIAL_FILE` 指向权限为 `0600` 的 secret 文件，内容不会进入 compose 环境文件、平台日志或 API 响应。发件地址与登录名必须相同且已获供应商授权。

SMTP 服务端接受仅表示“已提交 SMTP”，不表示进入收件箱或已读；SMTP 不提供跨供应商的幂等键，网络中断后的恢复是至少一次尝试，可能出现重复邮件。不要将它作为投递回执或计费依据。真实生产开通前分别用 Gmail、Outlook.com/Hotmail、网易 163 的隔离测试帐号记录授权、TLS、退信与退订验证结果。

事件密钥按用途分域：`engine_instance_events_secret` 只提供给 LimeSurvey engine，用于 engine 到 platform 的事件签名；安装和每次升级会按 `HMAC-SHA256(instanceSecret, "mjy-plugin-channel/v1/<engineInstanceId>")` 生成或刷新 `pubgw_engine_channel_secret`，publish gateway 只挂载该派生密钥。原始实例事件密钥不会进入 gateway 容器。`env.example` 中两项配置均为 secret 文件路径，不是密钥值。

## 生命周期

admin-web 的 nginx 在启动时解析 platform；Compose 要求 platform 健康后才启动 admin-web，避免上游 DNS 尚未注册导致反复退出。原健康探针和参数保持不变。

升级时先验证目标版本发布资产，将匹配主机架构的目标版本 bundle 解压到独立目录，并使用该目录中的 `./surveyctl` 执行升级；不要使用当前安装的旧版 CLI，旧版可能无法识别目标服务拓扑。以下源码路径命令仅在该源码与经过验证的目标版本一致时适用。

包含新增事件中继的目标版本升级失败且要求 restore-only 时，控制器会请求停止目标服务、保留经过验证的升级前备份并进入 `recovery_required`；它不会自动调用旧版恢复助手，因为中继停止不证明所有在途写入已静默。备份之后可能已有新写入，直接恢复升级前备份会丢失这些写入；在确认实例、数据库写入者状态并评估和处理备份后数据前，禁止自动覆盖。该状态不代表服务已确认停止或备份已恢复。控制器在首次暂存或备份之前持久记录 `upgrading`，并在 `upgrading` 与 `recovery_required` 两种未完成状态下拒绝普通 install、upgrade、rollback、backup、restore、uninstall、setup-probe 和会执行主动探测的 doctor；受控恢复入口尚不可用，不能通过重启新控制器绕过检查。已发布的旧版 CLI 和旧 helper 无法被这一门禁约束。

除升级状态外，新控制器在 install、upgrade、rollback、backup、restore、uninstall、setup-probe 以及执行主动业务探测的 doctor 首次实际变更前，还会持久记录未完成操作。操作失败或控制器中断后，结果可能未知，标记会阻止后续上述变更，即使进程锁已经释放；这不意味着子进程、Docker 操作或数据库写入已停止。纯参数校验失败且尚未开始变更时不会留下未完成标记。备份后的验证会启动子进程，在安装目录下创建私有临时目录并解密备份，因此在启动该验证前就会记录未完成操作；异常终止可能留下临时文件或存活的子进程，不能假定清理完成。`status` 和 `logs` 是只读查看命令，不属于这些主动变更入口；它们的结果也不能证明写入者静默。这两个命令要求 `current` 版本指针存在且与状态一致，首次安装失败时没有 `current`，它们会报指针错误，此时按下方“人工处置”中的只读命令查看。

出现未完成操作或 `recovery_required` 时，保留安装目录、操作标记、状态和备份供核查；**不要手动删除或修改标记，也不要用旧版 CLI 或 helper 绕过门禁**。当前没有受控解除或自动恢复入口；重启、锁释放或服务看似正常都不能证明恢复完成。旧备份之后可能存在新写入，恢复前须单独评估数据保留与丢失范围。

### 未完成操作与 `recovery_required` 的人工处置

当前版本没有解除命令，下面的步骤只用于**判断状态、保留现场和上报**，不会也不能让安装恢复可用。以下示例中 `TARGET=/opt/survey`，`VERSION` 取自 `state.json` 的 `version` 字段。

1. **判断属于哪种情况**（只读）：

   ```bash
   sudo cat "$TARGET/.surveyctl/state.json"
   sudo cat "$TARGET/.surveyctl/operation.intent"
   sudo cat "$TARGET/current"   # 首次安装失败时不存在
   ```

   | `state.json` 的 `status` | `operation.intent` | `current` | 含义 |
   | --- | --- | --- | --- |
   | `install_failed` | `pending`，`operation` 为 `install`、`install_version` 或 `install_offline` | 不存在 | 首次安装失败，例如 TLS 校验未通过；服务可能已部分启动，持久卷可能已创建 |
   | `upgrading` | `pending` | 指向旧版本 | 升级在暂存、备份或切换途中中断，结果未知 |
   | `recovery_required` | `pending` | 指向旧版本 | 升级失败且未能安全回到旧版本；`recoveryBackup` 是保留的升级前备份 |
   | 其他（如 `healthy`、`upgrade_failed`） | `pending` | 视情况 | backup、doctor、setup-probe 等操作失败或被中断，结果未知 |

2. **只读查看服务**：`current` 存在且与状态一致时用 `surveyctl status` 和 `surveyctl logs`；否则直接用该版本的 Compose 文件，只运行 `ps` 和 `logs`：

   ```bash
   REL="$TARGET/releases/$VERSION"
   sudo docker compose --project-directory "$REL" --env-file "$REL/.env" -f "$REL/compose.yml" ps
   sudo docker compose --project-directory "$REL" --env-file "$REL/.env" -f "$REL/compose.yml" logs --no-color --tail 200
   ```

3. **保留现场并上报**：把上述 `state.json`、`operation.intent` 的内容、`ps` 输出和截取的日志，连同执行的命令、时间和 `recoveryBackup` 名称，提交到 Wave 6 原生生命周期 issue（#33）或维护者指定的渠道。上报前删除日志中的密码、令牌和连接串；不要附上 `.env` 和 `shared/secrets/` 下的任何文件。

4. **在维护者给出处置前，不要做以下操作**：
   - 删除、修改或改名 `.surveyctl/` 下的任何文件，包括 `operation.intent`、`operation.lock`、`state.json`，以及 `current`；
   - 运行 `docker compose up`、`down`、`rm`、`stop`，尤其是带 `-v` 的命令，它会删除持久卷；
   - 换到另一个空目录重新 `install`：Compose 项目名固定为 `survey-production`，新安装会复用同名容器和数据卷；
   - 使用旧版 `surveyctl` 或直接调用 `restore.py`、`backup.py` 等 helper；
   - 在 `recovery_required` 下直接用 `recoveryBackup` 覆盖数据，备份之后的新写入会丢失。

受控恢复入口提供之前，这类安装只能由维护者在评估数据后人工处理；进入正式 Release 前需要补上受控恢复，或经维护者确认接受这一限制。

### 只读的新目标恢复计划

含此切片的控制器提供只读 `recovery-plan`，在失败安装没有 `current` 时也可观察已有现场：

```bash
sudo surveyctl --target /opt/survey recovery-plan --destination /opt/survey-recovered
# 可选：仅观察某份备份的 metadata；不会解密或验证备份认证
sudo surveyctl --target /opt/survey recovery-plan --destination /opt/survey-recovered --backup before-upgrade
```

目标必须尚不存在、无符号链接，并且不与原安装目录重叠；不会创建它。命令仅在已有私有锁上取得短暂 flock，读取有界文件并复查身份/元数据，不启动 Docker/helper、不读密钥、不写安装目录、目标或操作标记。异常、锁冲突、替换或不安全文件均失败关闭。旧控制器可能没有此命令；不要更换旧工具去绕过现有门禁。

输出始终为 `status=execution-blocked`、`executable=false`。退出 0 表示只读观察完成，**不是恢复或清理成功**。备份 metadata 指纹不代表 HMAC、签名、密文或卷内容验证；观察指纹不是授权、租约，也不能证明旧 helper/外部写入者停止。同权限写入者或管理员仍可能在检查后改变现场，不能把这份计划直接交给旧 restore helper 执行。

受控执行、原地恢复、标记解除及卸载仍保持既有阻断。后续按[受控恢复计划](../../../docs/superpowers/plans/2026-11-11-wave6-controlled-recovery.md)在全新隔离目标证明来源、helper/明文 containment、人员失效、数据和资源清理，再考虑接通执行；Release 工作流不在本切片修改范围。

### 全新目标的独占保留（不是恢复执行）

`recovery-reserve` 在明确指定、尚不存在的新目标的同一父目录内准备私有暂存，然后公布完整目标，不改变源安装。先用相同 `--destination` 和可选 `--backup` 执行上述 `recovery-plan`，人工核对源/目标，再把该次输出的 `source.snapshotSha256` 传给保留命令：

```bash
sudo surveyctl --target /opt/survey recovery-reserve \
  --destination /opt/survey-recovered \
  --observed-source-sha256 '<刚观察到的64位小写SHA256>'
```

直接父目录必须已存在、归执行用户所有且不可组/其他用户写入；全部祖先也检查属主和写权限，仅目标以外 root-owned sticky 临时祖先可例外。命令重新观察源并核对指纹，经目录描述符独占创建同父目录的 `.surveyctl-recovery-<随机ID>` 私有暂存及控制目录（0700）、reservation/lock/pending 文件（0600），生成随机 reservation ID 与项目名**提案**；落盘并复核后，才用不可覆盖的原子目录公布操作使指定目标出现，公布后再次落盘/复核。Ubuntu使用renameat2(RENAME_NOREPLACE)，macOS开发验证使用renameatx_np(RENAME_EXCL)；缺失接口或文件系统不支持时拒绝，不回退到可覆盖rename。项目提案不是已创建资源或授权；没有 Docker、网络、卷、数据库、密钥读取、备份认证或解密。

成功也始终为 `reserved-not-authorized`、`executable=false`。源指纹不是授权、租约或写入者隔离证明；源仍可能在观察后变化，未来执行必须重新验证。已有目标（含空目录）及公布期间出现的竞争目标一律不替换。首记录前中断只留下私有暂存，指定目标不会暴露可采纳的空目录；失败暂存不自动删除或接管，新重试另建暂存，旧残留保留。公布后失败则保留完整pending目标。控制器将`.surveyctl-recovery-<32位小写hex>`保留为内部命名空间，普通install拒绝该路径及其内部路径，即使目录仍为空；不要改名重用暂存或手工删除标记。不要删除 pending 或重用该目录绕过普通安装阻断。恢复执行、资源创建、应用启动与安全清理仍未接通。


安装、升级和回滚启动后，对仍处于 running/starting 的服务最多等待 120 秒；只有全部既有健康检查通过且 engine-init 成功退出才继续。unhealthy、缺失服务或初始化异常仍阻断，status 保持即时检查。本次修复的四节点完整生产生命周期尚待独立验收；原生 admin 探针诊断不能替代 readiness。

```bash
# 从已下载且已校验来源的 manifest 安装
platform/deploy/production/surveyctl --target /opt/survey install \
  --manifest /secure/release/survey-X.Y.Z-release.json \
  --public-host survey.example.com \
  --admin-user operations \
  --admin-email operations@example.com

platform/deploy/production/surveyctl --target /opt/survey status
platform/deploy/production/surveyctl --target /opt/survey setup-probe
platform/deploy/production/surveyctl --target /opt/survey doctor
platform/deploy/production/surveyctl --target /opt/survey backup --output manual-before-change
platform/deploy/production/surveyctl --target /opt/survey upgrade --manifest /secure/release/survey-X.Y.Z-release.json
platform/deploy/production/surveyctl --target /opt/survey uninstall
```

`uninstall` 默认保留持久卷、备份和密钥。彻底删除必须使用 `--purge-data` 并提供命令要求的精确二次确认文本。

## 备份与恢复

备份会暂停写入服务，使用 PostgreSQL custom dump 和 MariaDB `--single-transaction` dump，并归档 Caddy、平台资产、导出、发布网关状态及 LimeSurvey 上传/运行卷。载荷使用 AES-256-CBC + PBKDF2 加密并以独立 HMAC 验证，目录包含：

- `manifest.json`：应用版本、数据库 schema、Release manifest SHA-256、全部镜像 manifest digest 和精确文件 inventory。
- `SHA256SUMS`：所有加密载荷的 SHA-256。
- `CONTROL-HMAC`：使用与载荷加密密钥分域派生的密钥，认证 canonical manifest 与 `SHA256SUMS`。
- 两个数据库 dump 和七个卷归档的 `.enc` 文件。

**当前手动恢复入口已关闭**：`surveyctl restore` 与此版本的 `restore.py restore` 在检查或解密备份之前拒绝执行。该限制不约束已发布旧 helper、直接导入内部类或宿主机管理员。升级失败且策略为 `restore-only` 时同样不会调用旧恢复助手；保留备份和 `recovery_required` 状态，不能用旧控制器或直接调用 helper 绕过。原生验收中的恢复步骤因而保持阻断。

备份验证仍会认证控制元数据、inventory、checksum 和加密载荷；它会启动子进程并产生临时明文。直接调用 helper 的 `verify` 不经过控制器操作锁和 pending 标记，不能作为受控恢复或静默证明，勿在真实安装上绕开控制器调用。控制器异常终止时 pending 标记阻断后续变更，但不证明所有后代退出或临时明文已清理。独立进程树隔离、崩溃清理、写入者静默及操作人维护窗口尚未证明，故不能将已有 helper 的校验能力描述为安全恢复已完成。

`backup_encryption_key` 与数据同等重要：密钥丢失后备份不可恢复。密钥不得与备份保存在同一故障域；当前代码不替代外部离线保存和保留策略。

## Doctor

`doctor` 输出始终脱敏的 JSON，状态为 `ok`、`warning` 或 `blocked`。检查项包括：

- Compose 静态契约，以及 live `docker inspect` 端口绑定、网络挂载和宿主 `ss` 监听；80/443 的监听 PID 必须解析为指向已检查 edge 容器 IP/端口的 Docker forwarding process，仅 edge 可发布端口，数据库不得有宿主监听。
- 公网 TLS 证书、部署专用 marker 和路由。
- 完整容器集合、健康状态及一次性初始化退出码。
- Flyway schema、LimeSurvey 初始化状态、九个持久卷。
- 可完整认证、解密且通过 tar safety/兼容性检查的可恢复备份。
- 短期 JWT 登录、创建临时问卷、发布、公开作答、答卷查询、导出下载和清理的阻断式产品探针。

首次安装后、首次运行 `doctor` 前执行 `surveyctl setup-probe`。该正式命令使用内存中的 5 分钟 platform-operator JWT 创建隔离的最小权限租户、单席位套餐和 probe owner，注册生产 Engine，并核对后端签发的 per-instance event secret 与本机派生配置一致；只有全部成功才以 `0600` 写入 `<target>/shared/product-probe.json`。原始 JWT、master secret 和 event secret 均不会写入命令输出或 probe 配置。

也可由运营方在已有可发布问卷的租户中指定一个最小权限操作者，将以下非密钥配置保存为 `<target>/shared/product-probe.json`（文件内容不要放入日志）：

```json
{"tenantId":"<tenant UUID>","actorId":"<existing actor id>"}
```

探针只在内存中签发 5 分钟 JWT；临时项目在 `finally` 中归档，已发布的引擎问卷通过受 HMAC 保护的内部网关收口。配置缺失、任一业务步骤失败或清理失败都会使 `doctor` 返回 `blocked`。

缺少首次备份属于 `warning`；TLS、容器、网络暴露、迁移、卷或最小探针异常属于 `blocked`，命令返回退出码 4。

## 验证

日常 PR 和 main 由 `.github/workflows/ci.yml`（Survey CI）统一编排，完整质量门只调用一次。四镜像 AMD64/ARM64 只读验证抽取为 `build-images.yml`，供 CI 与手动 Release 验证复用；`release.yml`（Survey Release）只接受标签或手动入口，正式发布始终要求完整质量门，并保留扫描、签名、原生生命周期及 readiness 准出。

PR 的轻量范围使用明确白名单：根 README 与 docs/platform/docs 下的 Markdown 仅运行分流回归；仅 baseline-policy（可附带上述文档）运行产品化与生产发布门禁，跳过业务重型作业和镜像构建。其余文件均运行完整质量门与四镜像验证。改名同时考虑旧路径；文件 API 不可用、列表截断、提交身份变化或空列表均回退完整检查。main 始终执行完整质量门；是否试构建镜像仍按变更判断，手动 CI 执行完整检查和镜像验证。

稳定汇总检查名为 `Survey CI required`，始终核对变更计划、质量门与镜像构建的实际结果；失败、取消或非计划跳过都会阻断。该名称可供分支保护引用，本次编排变更不修改仓库保护规则。PR 标签只表示待办或冲突状态，不代表测试通过；main 推送会重新检查开放 PR，状态未知保留原冲突标签，权限/API 错误会明确失败。

```bash
PYTHONPYCACHEPREFIX=/tmp/survey-pyc \
  python3 -m unittest discover -s platform/deploy/production/tests

PYTHONPYCACHEPREFIX=/tmp/survey-pyc \
  python3 -m py_compile platform/deploy/production/{surveyctl,backup,restore,doctor}.py
```

`test_install_upgrade.py` 是不可 fake-success 的原生 clean-host 验收入口。它仅在 `SURVEY_PRODUCTION_E2E=1` 且提供两版真实 Release、DNS/TLS 和管理员参数时运行；否则明确 `SKIP`，不会用 fake 冒充成功。启用后实际执行 install -> setup-probe -> doctor/完整产品旅程 -> backup -> upgrade -> doctor/完整产品旅程 -> restore -> doctor/恢复后完整产品旅程 -> uninstall。

`native-acceptance.requirement.json` 是后续 multiarch Release workflow 必须消费的机器可读发布要求。真实发布前必须在 Ubuntu 22.04/24.04 的 AMD64/ARM64 runner 上完成四项矩阵，并验证正式 GHCR 镜像和 GitHub Release 制品。当前 Task 4 本地结果明确为“发布阻断、未验收”；开发机的可控 fake/fault-injection 测试只证明事务顺序和失败边界，不等同于该矩阵通过。

## GitHub Release 发布

`.github/workflows/release.yml` 只接受严格的 `vX.Y.Z-rc.N` 和 `vX.Y.Z` tag。候选版必须依次通过完整仓库质量门禁、三镜像 AMD64/ARM64 构建、Trivy、Cosign/GitHub attestation，以及 Ubuntu 22.04/24.04 × AMD64/ARM64 原生生命周期矩阵。任一 job 失败、跳过或证据身份不一致都不会进入 publication job。

RC Release 标记为 prerelease，并包含两架构 bundle、两层 checksum、三镜像 SBOM/Cosign bundle 和 native readiness。发布前后均使用下列命令重新核对下载后的全部资产：

```bash
platform/deploy/production/release/verify_release.sh \
  --assets /secure/star-survey-release \
  --repository lsgoodlionel/star-survey \
  --target-tag vX.Y.Z-rc.N \
  --source-tag vX.Y.Z-rc.N \
  --target-commit <40-character-tag-commit>
```

稳定版不是第二次构建。`vX.Y.Z` 只会选择同版本、已发布且通过升级矩阵的 `vX.Y.Z-rc.N`，重新下载所有资产并验证相同 checksum、镜像 digest、Cosign bundle、GitHub artifact attestation、SBOM 和 native readiness，然后逐字节复用 RC 资产。稳定 Release 页面会单独生成 `vX.Y.Z` 标题和验证命令，明确来源 RC、same bytes/digests、`upgradeVerified: true` 与稳定 tag commit；不会复用 RC 页面文本。稳定 tag 若指向不同源码提交，或 RC 不是 `upgrade-verified`，必须阻断推广。

publication 采用可恢复的幂等发布：先查询现有 Release，规范化核对 tag、title、body、prerelease、draft 和 target commit，再逐个下载已有资产并比较字节；只上传缺失资产，不使用 `--clobber`。draft 的 title/body/prerelease 漂移可按受审内容修复，target commit 漂移始终阻断；已发布 Release 的任意 metadata 漂移直接失败且不会覆盖。若 `gh release create` 返回未知结果，会先重新查询再继续；任何同名不同内容或多余资产都会阻断。

本地 verifier 递归枚举下载目录的所有 entry。除了清单精确列出的单链接普通文件，不允许额外目录、嵌套文件、symlink、hardlink、FIFO/socket/device，也拒绝大小写或 Unicode NFC 冲突路径。

当前状态（2026-11-09 核对）：仓库已有 `v0.3.0-rc.1` 至 `v0.3.19-rc.1` 的候选 tag，尚无稳定 tag。`v0.3.19-rc.1`（commit `46766fd432`）在 [run 41942118701](https://github.com/lsgoodlionel/star-survey/actions/runs/41942118701) 中完成四镜像多架构构建、扫描、Cosign 签名与 attestation，以及四节点原生生命周期和升级验证，详见 Wave 6 的原生生命周期与 Release 准出记录；同一 run 的 GitHub Release 发布步骤为 skipped，仓库还没有 GitHub Release 对象。创建稳定 tag 前仍需人工核对公网 TLS、备份密钥异地保存和 Release 资产。

### Engine 运行镜像（2026-10-30）

Engine 使用 Alpine 3.24 同源 PHP 8.3 CLI/Apache 包，保留 UID 10001、80 端口、`php`、`apache2-foreground`、`/var/www/html` 和既有静态附件防护。镜像仅安装运行包，构建时升级安全包；可传 `APK_MIRROR` 选择已测速源，仓库默认保留官方源及索引更新失败回退。

本地构建后执行 `python3 platform/deploy/production/release/engine_image_smoke.py <image>`，验证非 root、PHP/session、健康检查和附件访问边界。发布仍须按漏洞策略运行固定版本 Trivy；不忽略未修复 HIGH/CRITICAL。此次 ARM64 完整镜像本地扫描 0 阻断项，发布相关测试 59/59；双架构和线上检查须以 PR CI 结果为准，不能代替正式 RC 原生生命周期验收。

### 2026-11-01 原生安装路径修复

`v0.3.5-rc.1` 四个原生节点在 install 阶段失败。安装器曾把 Compose 的项目目录指向安装根目录，使 `./init/` 和 `./Caddyfile` 等相对挂载偏离已校验的版本目录；缺失脚本被创建为目录后，shell 可退出 0，服务持续重启，PostgreSQL 初始化脚本也无法执行。安装器现以所选 `releases/<version>` 作为 Compose 工作目录，确保安装、升级和恢复使用对应版本的文件。Compose 固定项目名保持不变，数据卷身份不变。

回归测试先失败后通过；production suite 111 项中 110 通过、1 项原生 opt-in 跳过，Release suite 60/60 通过。Docker 实测确认错误相对路径解析和 shell 对目录退出 0。完整四节点生命周期仍须人工合并后以另行授权的新不可变候选重新验收；本修复不提升生产状态。
