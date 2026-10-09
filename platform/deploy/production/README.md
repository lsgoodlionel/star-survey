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

- `manifest.json`：应用版本、数据库 schema 和精确文件 inventory。
- `SHA256SUMS`：所有加密载荷的 SHA-256。
- 两个数据库 dump 和七个卷归档的 `.enc` 文件。

恢复先校验路径、inventory、checksum、加密认证和 Release 声明的 schema 兼容范围，再恢复到随机命名的隔离 Compose project。隔离栈全部健康后会创建自动安全备份，随后才停止现有生产 project 并写入生产卷。隔离验证失败不会停止或改写现有栈；生产应用阶段失败会自动回灌安全备份并恢复旧栈，安全备份保留供人工复核。

`backup_encryption_key` 与数据同等重要：密钥丢失后备份不可恢复。密钥不得与备份保存在同一故障域；当前代码不替代外部离线保存和保留策略。

## Doctor

`doctor` 输出始终脱敏的 JSON，状态为 `ok`、`warning` 或 `blocked`。检查项包括：

- 仅 edge 发布 80/443，数据库仍位于 internal 网络。
- 公网 TLS 证书、部署专用 marker 和路由。
- 完整容器集合、健康状态及一次性初始化退出码。
- Flyway schema、LimeSurvey 初始化状态、九个持久卷。
- 可读且 checksum 正确的备份，以及不创建永久用户的只读最小探针。

缺少首次备份属于 `warning`；TLS、容器、网络暴露、迁移、卷或最小探针异常属于 `blocked`，命令返回退出码 4。

## 验证

```bash
PYTHONPYCACHEPREFIX=/tmp/survey-pyc \
  python3 -m unittest discover -s platform/deploy/production/tests

PYTHONPYCACHEPREFIX=/tmp/survey-pyc \
  python3 -m py_compile platform/deploy/production/{surveyctl,backup,restore,doctor}.py
```

`test_install_upgrade.py` 是原生 clean-host 验收入口。它仅在 `SURVEY_PRODUCTION_E2E=1` 且提供两版真实 Release、DNS/TLS 和管理员参数时运行；否则明确 `SKIP`，不会用 fake 冒充成功。启用后实际执行 install -> doctor -> 公网只读探针 -> backup -> upgrade -> doctor -> 隔离恢复 -> uninstall。

真实发布前仍必须在 Ubuntu 22.04/24.04 的 AMD64/ARM64 runner 上完成该矩阵，并验证正式 GHCR 镜像和 GitHub Release 制品。开发机的可控 fake/fault-injection 测试只证明事务顺序和失败边界，不等同于该矩阵通过。
