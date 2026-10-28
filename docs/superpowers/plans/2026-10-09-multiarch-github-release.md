# Multi-architecture GitHub Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 GitHub Actions 构建、原生验证、签名并发布 AMD64/ARM64 生产镜像与 Ubuntu 一键安装包，支持候选版和稳定版推广。

**Architecture:** Buildx 构建 GHCR 多架构 manifest，生成 digest 固定的 `release.json`；各 Ubuntu/arch 原生 runner 下载同一候选制品并执行 install/doctor/product probe/upgrade/restore/uninstall。只有所有门禁通过后才创建 GitHub Release，并附带 checksum、Cosign bundle、GitHub artifact attestation、SBOM 和版本说明。

**Tech Stack:** GitHub Actions、Docker Buildx、GHCR、Syft、Trivy、Cosign keyless signing、GitHub artifact attestations、GitHub CLI。

**Spec:** `docs/superpowers/specs/2026-10-09-platform-productization-production-upgrade-design.md`

## Global Constraints

- 工作流使用最小 `permissions`；只有 release job 拥有 `contents: write`，签名/证明 job 按需拥有 `id-token: write` 和 `attestations: write`。
- 不使用长期签名私钥；采用 GitHub OIDC 的 keyless 签名和 artifact attestation。
- `vX.Y.Z-rc.N` 只发 prerelease；稳定 `vX.Y.Z` 必须促进已验证的候选制品，不重新构建不同字节。
- 禁止使用 mutable image tag 作为安装依据；tag 仅供人读，`release.json` 必须记录 manifest digest。

## Review Focus

- GitHub 托管 ARM runner 标签使用当前官方支持的 `ubuntu-22.04-arm` 和 `ubuntu-24.04-arm`；任何标签更改必须重新查证官方文档。
- 安装包的 checksum、签名和 attestation 必须在发布前由独立 job 下载验证，不能只生成不验证。
- 漏洞扫描的阻断级别和例外必须版本化，例外需要到期时间和根因。

---

### Task 1: Release manifest and deterministic bundle

**Files:**
- Create: `platform/deploy/production/release/build_release.py`
- Create: `platform/deploy/production/release/render_notes.py`
- Create: `platform/deploy/production/release/tests/test_build_release.py`
- Modify: `platform/deploy/production/release.schema.json`
- Create: `platform/deploy/production/VERSION`

**Interfaces:** assets `star-survey-VERSION.tar.gz`, `release.json`, `SHA256SUMS`, `sbom-*.spdx.json`, `*.sigstore.json`, `release-notes.md`.

- [ ] **Step 1: Write failing reproducibility and schema tests**

固定文件顺序、mtime、owner/mode，断言两次构建的 tarball checksum 相同；release manifest 含版本、commit、兼容范围、镜像 digest、支持 OS/arch 和资产 checksum。

- [ ] **Step 2: Implement bundle builder and notes renderer**

只收录 Production Compose、Caddy、`surveyctl`、schema、示例配置和运维文档；排除测试 fixture、运行时凭据和本地备份。

- [ ] **Step 3: Verify and commit**

Run: `python3 -m unittest discover -s platform/deploy/production/release/tests`

```bash
git add platform/deploy/production/release platform/deploy/production/release.schema.json platform/deploy/production/VERSION
git commit -m "build: add deterministic production release bundle"
```

### Task 2: Multi-architecture image workflow

**Files:**
- Create: `.github/workflows/release.yml`
- Create: `.github/release/vulnerability-policy.json`
- Create: `platform/deploy/production/release/tests/test_workflow.py`

- [ ] **Step 1: Write failing workflow policy tests**

检查 path/tag 触发、concurrency、最小权限、固定 action major/version、三个镜像的 amd64/arm64 build、SBOM/provenance、digest 输出和禁止 `latest`。

- [ ] **Step 2: Implement build, scan and sign jobs**

先跑仓库现有 `platform-quality.yml` 同级门禁，再 build/push GHCR；对 manifest digest 执行 Trivy，用 Cosign OIDC 签名并保存 bundle。

- [ ] **Step 3: Verify workflow statically and by dry-run branch**

Run: `python3 -m unittest platform/deploy/production/release/tests/test_workflow.py`

在非 tag `workflow_dispatch` 中允许 build/test 但禁止创建 Release。

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml .github/release platform/deploy/production/release/tests
git commit -m "ci: build signed multiarch production images"
```

### Task 3: Native Ubuntu release acceptance

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `platform/deploy/production/release/native_acceptance.sh`
- Create: `platform/deploy/production/release/tests/test_native_acceptance.py`

- [ ] **Step 1: Write failing matrix and artifact-consumption tests**

矩阵必须包含 `ubuntu-22.04`、`ubuntu-24.04`、`ubuntu-22.04-arm`、`ubuntu-24.04-arm`；每个 job 必须消费相同 digest 和 tarball checksum。

- [ ] **Step 2: Implement native install/upgrade/restore test**

在隔离目录运行 `surveyctl install`、`doctor`、最小业务探针、`backup`、候选升级、恢复到第二项目和 `uninstall`；失败时上传脱敏 doctor/log 产物。

- [ ] **Step 3: Verify and commit**

Run: `python3 -m unittest platform/deploy/production/release/tests/test_native_acceptance.py`

```bash
git add .github/workflows/release.yml platform/deploy/production/release
git commit -m "ci: verify releases on native ubuntu architectures"
```

### Task 4: Attestation and GitHub Release publication

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `platform/deploy/production/release/verify_release.sh`
- Create: `platform/deploy/production/release/tests/test_release_publication.py`
- Modify: `platform/deploy/production/README.md`
- Modify: `platform/README.md`

- [ ] **Step 1: Write failing publication-gate tests**

只有 semver tag 且所有 native jobs 成功时发布；`rc` 标记 prerelease；稳定版引用已通过的 rc digest；任何签名/证明/checksum 失败都禁止发布。

- [ ] **Step 2: Add artifact attestations and independent verification**

对 tarball、release manifest、checksum 和 SBOM 生成 GitHub attestation；在发布 job 中重新下载并运行 `verify_release.sh`。

- [ ] **Step 3: Publish with GitHub CLI and attach all assets**

使用 `gh release create`/`gh release upload --clobber=false`，不在未知结果下重复创建；发布说明包含安装、升级、回滚限制、已知问题和验证命令。

- [ ] **Step 4: Verify candidate release before stable promotion**

创建首个 `v0.x.y-rc.1`，验证 GitHub Release 资产、GHCR manifest、Cosign/GitHub attestation 和四组 Ubuntu/arch 安装证据；未通过不创建稳定 tag。

- [ ] **Step 5: Update docs and commit**

```bash
git add .github/workflows/release.yml platform/deploy/production platform/README.md
git commit -m "ci: publish verified github release assets"
```
