# secure-docs：公开仓库中的敏感文档加密

本仓库是**公开**的，但部分 P0 证据类文档不适合明文公开：它们包含自家系统的攻击面与绕过手法、成本与定价输入、以及待法务确认的法律暴露。这些文档只以密文（`.md.gpg`）进入仓库，明文由 `.gitignore` 挡住。

哪些文档属于此类，见 [`sensitive.txt`](sensitive.txt)。

## 用法

```bash
platform/tools/secure-docs/secure-docs.sh status   # 查看当前是明文还是密文
platform/tools/secure-docs/secure-docs.sh unlock   # 解密以便阅读或编辑
platform/tools/secure-docs/secure-docs.sh lock     # 重新加密并删除明文（提交前必做）
```

加密方式为 GnuPG 对称加密（AES-256，自带完整性校验）。

## 密钥

默认从 `~/Develop/star-docs.key` 读取，可用环境变量 `STAR_DOCS_KEY_FILE` 指向别处。

- **密钥丢了文档就永久打不开**，请立刻备份到密码管理器。
- 密钥文件权限应为 600，且必须在仓库之外——不要为了方便挪进仓库。
- 需要给他人阅读权限时，通过安全渠道单独交付密钥，不要发在聊天或工单里。

## 工作习惯

编辑这些文档时先 `unlock`，改完 `lock` 再提交。`lock` 会删除明文，因此提交里只会出现 `.md.gpg`。如果 `status` 显示 `both`，说明上次忘了 `lock`，执行一次即可。

## 重要：加密不能追溯

这些文档在加密前**已经以明文推送过**，因此仍留在 git 历史里，任何人都能从历史中取出。加密只保护此后的版本。

要彻底移除历史中的明文，需要重写历史并强制推送，且这只在"确认没有人克隆或派生过本仓库"时才有意义——GitHub 在一段时间内仍可能通过提交 SHA 访问到悬空对象。是否重写历史请由仓库所有者决定。
