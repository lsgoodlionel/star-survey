# Survey 单机生产部署

本目录是 Ubuntu 22.04/24.04、AMD64/ARM64 的 Docker Compose 单机生产运行时。生产安装只消费
Release manifest、校验过的架构 bundle 和固定 digest 镜像；服务器不需要源码，也不会在镜像拉取失败时回退到本地构建。

## 前提

- systemd 正常运行，Docker Engine >= 24，Docker Compose plugin >= 2.20。
- 至少 4 GiB 内存、目标文件系统至少 20 GiB 可用空间。
- 公网 DNS 已指向本机或显式声明的 NAT 地址，80/443 可用，系统时间已同步。
- Release manifest、bundle 和镜像必须来自同一发布版本。在线安装必须提供 manifest SHA-256；离线包必须完成同等校验。
- 管理员密码和所有服务密钥由安装过程生成在 `<target>/shared/secrets`，目录权限 `0700`、文件权限 `0600`。不要把该目录写入日志、工单或仓库。

## 生命周期

```bash
# 从已下载且已校验来源的 manifest 安装
platform/deploy/production/surveyctl --target /opt/survey install \
  --manifest /secure/release/survey-X.Y.Z-release.json \
  --public-host survey.example.com \
  --admin-user operations \
  --admin-email operations@example.com

platform/deploy/production/surveyctl --target /opt/survey status
platform/deploy/production/surveyctl --target /opt/survey doctor
platform/deploy/production/surveyctl --target /opt/survey backup --output manual-before-change
platform/deploy/production/surveyctl --target /opt/survey upgrade --manifest /secure/release/survey-X.Y.Z-release.json
platform/deploy/production/surveyctl --target /opt/survey restore manual-before-change
platform/deploy/production/surveyctl --target /opt/survey uninstall
```

`uninstall` 默认保留持久卷、备份和密钥。彻底删除必须使用 `--purge-data` 并提供命令要求的精确二次确认文本。

## 备份与恢复

备份会暂停写入服务，使用 PostgreSQL custom dump 和 MariaDB `--single-transaction` dump，并归档 Caddy、平台资产、导出、发布网关状态及 LimeSurvey 上传/运行卷。载荷使用 AES-256-CBC + PBKDF2 加密并以独立 HMAC 验证，目录包含：

- `manifest.json`：应用版本、数据库 schema、Release manifest SHA-256、全部镜像 manifest digest 和精确文件 inventory。
- `SHA256SUMS`：所有加密载荷的 SHA-256。
- `CONTROL-HMAC`：使用与载荷加密密钥分域派生的密钥，认证 canonical manifest 与 `SHA256SUMS`。
- 两个数据库 dump 和七个卷归档的 `.enc` 文件。

恢复先校验路径、认证控制元数据、inventory、checksum、载荷加密认证、应用版本/schema 兼容范围及 Release/镜像身份。七个卷归档会在任何 Docker 操作前逐成员检查，只允许规范化相对路径的普通文件和目录；路径穿越、链接、特殊文件、重复/大小写冲突、成员数/体积超限和异常膨胀率均会阻断。随后才恢复到随机命名的隔离 Compose project。隔离栈全部健康后会创建自动安全备份，随后才停止现有生产 project 并写入生产卷。隔离验证失败不会停止或改写现有栈；生产应用阶段失败会自动回灌安全备份并恢复旧栈，安全备份保留供人工复核。

`backup_encryption_key` 与数据同等重要：密钥丢失后备份不可恢复。密钥不得与备份保存在同一故障域；当前代码不替代外部离线保存和保留策略。

## Doctor

`doctor` 输出始终脱敏的 JSON，状态为 `ok`、`warning` 或 `blocked`。检查项包括：

- Compose 静态契约，以及 live `docker inspect` 端口绑定、网络挂载和宿主 `ss` 监听；仅 edge 可发布 80/443，数据库不得有宿主监听。
- 公网 TLS 证书、部署专用 marker 和路由。
- 完整容器集合、健康状态及一次性初始化退出码。
- Flyway schema、LimeSurvey 初始化状态、九个持久卷。
- 可完整认证、解密且通过 tar safety/兼容性检查的可恢复备份。
- 短期 JWT 登录、创建临时问卷、发布、公开作答、答卷查询、导出下载和清理的阻断式产品探针。

产品探针不创建永久测试用户。运营方需在已有可发布问卷的租户中指定一个最小权限操作者，将以下非密钥配置保存为 `<target>/shared/product-probe.json`（文件内容不要放入日志）：

```json
{"tenantId":"<tenant UUID>","actorId":"<existing actor id>"}
```

探针只在内存中签发 5 分钟 JWT；临时项目在 `finally` 中归档，已发布的引擎问卷通过受 HMAC 保护的内部网关收口。配置缺失、任一业务步骤失败或清理失败都会使 `doctor` 返回 `blocked`。

缺少首次备份属于 `warning`；TLS、容器、网络暴露、迁移、卷或最小探针异常属于 `blocked`，命令返回退出码 4。

## 验证

```bash
PYTHONPYCACHEPREFIX=/tmp/survey-pyc \
  python3 -m unittest discover -s platform/deploy/production/tests

PYTHONPYCACHEPREFIX=/tmp/survey-pyc \
  python3 -m py_compile platform/deploy/production/{surveyctl,backup,restore,doctor}.py
```

`test_install_upgrade.py` 是不可 fake-success 的原生 clean-host 验收入口。它仅在 `SURVEY_PRODUCTION_E2E=1` 且提供两版真实 Release、DNS/TLS、管理员参数和已配置产品探针身份时运行；否则明确 `SKIP`，不会用 fake 冒充成功。启用后实际执行 install -> doctor/完整产品旅程 -> backup -> upgrade -> doctor/完整产品旅程 -> restore -> doctor/恢复后完整产品旅程 -> uninstall。

`native-acceptance.requirement.json` 是后续 multiarch Release workflow 必须消费的机器可读发布要求。真实发布前必须在 Ubuntu 22.04/24.04 的 AMD64/ARM64 runner 上完成四项矩阵，并验证正式 GHCR 镜像和 GitHub Release 制品。当前 Task 4 本地结果明确为“发布阻断、未验收”；开发机的可控 fake/fault-injection 测试只证明事务顺序和失败边界，不等同于该矩阵通过。
