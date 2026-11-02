# Wave 6 候选制品与发布晋升

标签推送仅构建候选、签名并在四个原生环境验收，不创建 GitHub Release。bootstrap 仅用于创建后续升级测试的前代，不能发布；正式候选必须通过从已验证前代升级及全部四节点验收。实际标签、GHCR 镜像和 Release 均需单独授权；本说明不授权执行。

## Actions 前代

在 `.github/release/baseline-policy.json` 中为后续版本提交经审查的规则，模式 `predecessor-artifact`，字段 `version`、`runId`（整数）、`commit`（40 位小写 SHA）及 `manifestSha256`（64 位小写 SHA）。值须来自同仓库、同一 release.yml 的成功标签 push 和其已签名制品，而非自由 URL。候选组装校验 run、工作流、标签、commit、清单散列和原有签名/attestation/原生证据，再执行前代安装与升级。不要在产物产生前猜测这些值；Actions 制品有保存期限。

## 人工记录与晋升

先由人工完成 #41 的实际 VoiceOver 测试及独立复核，并在 #41 留下仓库所有者签署的证据评论，绑定候选 run、tag、commit、manifest 散列。格式如下，测试者和复核者必须为不同的可识别人员；末行写明真实测试证据（至少 25 字符），不要填写占位文字：

```text
SURVEY-VOICEOVER-ACCEPTANCE v1
run: <run-id>
tag: <rc-tag>
commit: <40-lowercase-hex>
manifest-sha256: <64-lowercase-hex>
result: passed
coverage: editor,keyboard,focus,errors,save,preview,publish,respond
method: manual-voiceover
tester: <identifier>
independent-reviewer: <identifier>
evidence: <actual evidence and review location>
```

待 #41 依既定流程完成并关闭后，仓库所有者在同一 issue 再发表**针对该候选的明确发布批准**。其中 evidence ID 指上面评论，证据 SHA-256 是该评论正文 UTF-8 原始字节的摘要：

```text
SURVEY-RELEASE-APPROVAL v1
run: <run-id>
tag: <rc-tag>
commit: <40-lowercase-hex>
manifest-sha256: <64-lowercase-hex>
voiceover-result: passed
voiceover-evidence: https://github.com/lsgoodlionel/star-survey/issues/41#issuecomment-<id>
decision: approve-publication
voiceover-evidence-sha256: <64-lowercase-hex>
```

父任务取得用户对具体 Release 的明确授权后，仓库所有者才可从 `main` 手动触发 release.yml，填写对应 run、tag、commit、manifest 散列和两条评论 ID。工作流检查同仓库原始成功 run 与标签、已签名制品、四节点升级验收、当前关闭的 #41、两条所有者评论与一致性，并在发布前重新检查。记录本身是责任人的人工陈述，不是自动读屏测试，不能代替实际操作和独立复核。该入口只支持候选预发布；稳定版仍需单独设计和授权。

权限差异：仅 `candidate-assets` 新增作业级 `actions: read`；独立人工晋升作业使用 `actions: read`、`contents: write`、`packages: read`，与原已禁用发布作业所需范围一致；不引入 PAT、持久凭据或仓库默认权限变更。
