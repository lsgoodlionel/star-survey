# Productization Requirements Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立与当前代码、前端路由、生产验证和 305 条需求矩阵对应的可校验产品化基线，并统一仓库、外部蓝图和 Obsidian 口径。

**Architecture:** 以 JSON 能力映射作为机器事实来源，通过 Python 标准库生成 Markdown 人读视图；校验器检查需求 ID、API 路径、前端路由、证据文件和状态转移，避免手工复制出现多个真相。

**Tech Stack:** JSON Schema 2020-12、Python 3.12 标准库、Markdown、unittest。

**Spec:** `docs/superpowers/specs/2026-10-09-platform-productization-production-upgrade-design.md`

## Global Constraints

- `02-全量需求追踪矩阵.md` 保持需求编号和原始语义；不重排 ID，不因实施方便删需求。
- `capabilities.json` 只记录可核对事实；人读 Markdown 由生成器产生，不手工维护同一张表。
- 状态只允许 `accepted|partial|not_started|external`；`accepted` 必须同时引用 API/前端/流程/生产证据。
- 外部蓝图文件仅更新已有 `04`、`08` 和 `03` 的执行指向，不再新建重复的顶层进度文档。

## Review Focus

- 生成器重复运行必须字节一致，并在引用文件不存在或状态无证据时失败。
- 每个产品能力同时显示「后端已有」「前端已有」「生产已验证」三类状态，不用一个百分比掩盖差异。
- 报告基线 `4f48983e` 的 13/86/191/15 只作为起点；任何数字变化都需要证据 diff。

---

### Task 1: Machine-readable capability map

**Files:**
- Create: `platform/docs/productization/capabilities.schema.json`
- Create: `platform/docs/productization/capabilities.json`
- Create: `platform/tools/productization/render_capabilities.py`
- Test: `platform/tools/productization/test_render_capabilities.py`

**Interfaces:** `capabilities.json` 每项必含 `id/title/requirementIds/backend/frontend/flow/production/status/evidence/nextWave`；生成器输出 `platform/docs/productization/capability-map.md`。

- [ ] **Step 1: Write failing schema and deterministic-render tests**

覆盖重复 ID、未知需求 ID、不存在的证据路径、无六层证据却标记 `accepted` 和非确定输出。

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest platform/tools/productization/test_render_capabilities.py`

Expected: FAIL because schema, source data and renderer do not exist.

- [ ] **Step 3: Implement schema, renderer and initial map**

首批至少覆盖工作台、编辑、导入、预览、审批/发布、投放链接/二维码、答卷/摘要、导出、模板/品牌、通讯录/投放、管理和生产部署。对每项区分「后端已完成」和「产品已完成」。

- [ ] **Step 4: Verify**

Run: `python3 -m unittest platform/tools/productization/test_render_capabilities.py && python3 platform/tools/productization/render_capabilities.py --check`

Expected: PASS; generated Markdown is clean.

- [ ] **Step 5: Commit**

```bash
git add platform/docs/productization platform/tools/productization
git commit -m "docs: add verified productization capability baseline"
```

### Task 2: Requirements, blueprint and release roadmap

**Files:**
- Create: `platform/docs/productization/frontend-backend-requirements.md`
- Create: `platform/docs/productization/product-blueprint.md`
- Create: `platform/docs/productization/release-roadmap.md`
- Modify: `platform/README.md`
- Modify: `platform/docs/p2/progress.md`
- Modify: `platform/docs/traceability/requirement-tests.md`
- Modify: `platform/tools/productization/test_render_capabilities.py`

- [ ] **Step 1: Add failing cross-document reference tests**

要求三份文档引用本设计、总计划、能力映射和报告，并明确 Wave 0-6、冻结范围、出版条件和责任边界。

- [ ] **Step 2: Write the three canonical human-readable documents**

`frontend-backend-requirements.md` 写角色/正常流/失败流/验收；`product-blueprint.md` 写信息架构、导航、问卷内工作流和引擎边界；`release-roadmap.md` 写波次、依赖、准入/准出和延后项。

- [ ] **Step 3: Sync repository status documents**

在 README/进度/追溯中引用新基线，保留报告的真实完成数字，不预告未通过验收的交付。

- [ ] **Step 4: Verify and commit**

Run: `python3 -m unittest platform/tools/productization/test_render_capabilities.py && python3 platform/tools/productization/render_capabilities.py --check`

```bash
git add platform/README.md platform/docs platform/tools/productization
git commit -m "docs: align requirements blueprint and release roadmap"
```

### Task 3: External blueprint and Obsidian synchronization

**Files:**
- Modify: `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/03-实施与验收计划.md`
- Modify: `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/04-开发方案总蓝图（仓库校准版）.md`
- Modify: `/Users/lionel/Documents/MJY 2/LimeSurvey本土化方案/08-需求进度与方案修订（仓库实证）.md`
- Modify: registered Obsidian project overview and roadmap from migration memory

- [ ] **Step 1: Re-read current external files and migration memory**

Run: `sed -n '1,240p' ~/.codex/memories/claude-code-migration/README.md` and locate the registered Obsidian project files. Do not infer paths from an old conversation.

- [ ] **Step 2: Update only execution status and references**

把 `03` 标记为历史计划并指向新总计划；用当前仓库证据更新 `04/08`，不重写原始需求。

- [ ] **Step 3: Record traceability**

在 Obsidian 记录日期、分支、提交、验证命令、交付和下一步，并链接对应 GitHub PR。

- [ ] **Step 4: Commit repository changes and report external-file diffs separately**

仓库内变更正常提交；外部蓝图/Obsidian 不借用仓库提交。
