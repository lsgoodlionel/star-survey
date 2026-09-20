# logship：服务器日志采集与故障上报

出故障时自动把**已脱敏**的日志打成证据包，提交到一个专用 GitHub 仓库，开发直接从仓库拉取排查。

工具与应用解耦：应用只需往 spool 目录写一个 JSON，采集、脱敏、限流、上传都由本工具负责。这样 GitHub 令牌只存在于一处，也便于多个应用共用同一个日志仓库。只依赖 Python 3 标准库（开发验证于 3.9）。

## 仓库里的位置约定

```text
incidents/<应用>/<年>/<月>/<日>/<时间戳>-<指纹>/
    manifest.json     # 可直接在仓库里 grep：应用、实例、环境、版本、错误摘要、指纹、压制次数
    bundle.tar.gz     # manifest.json + logs/<来源名>（均已脱敏）
```

开发排查时：按应用和日期找目录，或直接搜 `manifest.json` 里的错误摘要与指纹；同一指纹的目录就是同一类故障。

## 使用

```bash
export MJY_LOGSHIP_TOKEN=<细粒度 PAT>
python3 -m logship.cli doctor --config /etc/logship/survey.json   # 体检：配置、来源可读性、令牌
python3 -m logship.cli ship   --config /etc/logship/survey.json --summary "CDbException: ..." --dry-run
python3 -m logship.cli drain  --config /etc/logship/survey.json   # 处理应用写入 spool 的故障
```

`drain` 适合放进 cron 或 systemd timer（建议每分钟一次）。退出码：`0` 正常，`1` 上报失败或体检不通过，`2` 配置或令牌问题。

### 应用侧怎么触发

在应用的错误处理里写一个 JSON 到 spool 目录即可，至少包含 `errorSummary`：

```php
file_put_contents(
    '/var/lib/logship/spool/' . uniqid('incident-', true) . '.json',
    json_encode(['errorSummary' => $exception->getMessage(), 'occurredAt' => gmdate('c')])
);
```

应用进程不需要网络权限，也不需要接触令牌。

## 配置

见 `config.example.json`。要点：

- `tokenEnv`：令牌所在的**环境变量名**。配置文件里即使写了 `token` 也不会被使用。
- `sources`：`kind` 为 `file`（文件尾部）或 `docker`（`docker logs --tail`）。任一来源取不到只记一行说明，不影响其他来源。
- `limits`：`cooldownSeconds` 同一指纹的冷却时间，`dailyLimit` 每日上限，`maxLinesPerSource` 每个来源保留的尾部行数，`maxBundleBytes` 整包上限（超限直接拒绝，不静默截断）。
- `redaction`：`maskIp` 是否屏蔽 IP，`extraPatterns` 追加自定义脱敏规则（如内网主机名）。

## 脱敏

上传前强制执行，不可关闭。默认清除：密码/密钥/令牌类字段值、`Authorization` 头、Cookie 与会话 id、GitHub 令牌、JWT、邮箱、手机号、身份证号、长数字串（银行卡）。保留字段名、异常类名、文件与行号、traceId——即抹掉值、保留形状。

即使日志仓库是私有的，也**不要**把整包原始访问日志传上去：问卷类应用的日志里可能含答卷内容。只传错误上下文。

## 令牌

创建细粒度 PAT，仅对日志仓库授予 `Contents: Read and write`，不要授予其他仓库或其他权限。放进服务器环境变量或 systemd 的 `EnvironmentFile`（权限 600）。令牌不会写入日志，异常信息里也会被替换掉。

## 测试

```bash
cd platform/tools/log-shipper && python3 -m unittest discover -s tests -t .
```
