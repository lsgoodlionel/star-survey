# Business Dashboard v1 跟进审计

日期：2026-10-11

分支：`feat/admin-product-alignment`

本文件是 [`14-full-development-report.md`](14-full-development-report.md) 的增量说明。原报告仍保留为
2026-10-09 的历史基线；以下结论以当前代码和 Task 7 真实栈证据为准。

## 1. 本次交付边界

- `/` 与成功登录进入 `/dashboard`；`/workspace` 继续承担完整项目、文件夹和问卷管理。
- Dashboard 从单个授权服务端快照展示业务摘要、待办、问卷运行状态、真实操作入口和最近工作。
- 最近工作由服务端按租户、角色和资源权限过滤；页面往返后可恢复，reload 后按 memory-only JWT 安全约束
  重新认证仍可恢复。
- 发布页已经连接投放链接、二维码和答卷入口；答卷页提供摘要、明细和导出任务。
- 待审批任务只对具有项目级 `publish_reviewer` 授权的真实成员可见；同项目 `editor` 不可见也不可审批。
- 项目级 `raw_data_viewer` 作为现有等价数据角色，只看到获授权项目的统计、答卷、导出数量与动作，看不到审批、
  发布或预览区块。

## 2. 已被新证据取代的结论

| 2026-10-09 基线判断 | 当前事实 |
|---|---|
| 资源工作台没有总览、待办或状态摘要 | 新增独立 Dashboard，显示授权摘要、任务、问卷运行状态和最近工作；资源工作区职责保持不变。 |
| 发布页没有答卷链接、二维码、回收状态、答卷数量或导出入口 | 发布后可创建真实投放链接并展示后端二维码，可进入答卷页；答卷摘要、明细和真实导出任务已有页面。 |
| 答卷列表、摘要、字段只有 API、无页面 | `ResponsesPage` 已接入真实摘要、明细、字段与权限状态。 |
| 导出任务和下载只有 API、无页面 | 答卷页已提供格式选择、任务进度、取消、重试和下载；格式覆盖边界仍以 capability map 为准。 |
| 发布后没有投放、答卷和导出的连续用户路径 | 真实 Chromium 已贯通发布、正式作答、答卷读取、导出下载以及 Dashboard 往返。 |

这些变化不回写原报告，也不把复合需求或能力提升为 `accepted`；生产层证据仍未形成。

## 3. 仍然成立的缺口

- Wave 2：模板浏览、复制和品牌配置仍未形成管理端闭环。
- Wave 4：通讯录、部门、受众、批量投放、催答和退订仍缺完整运营页面与真实供应商闭环。
- Wave 5：Phase C 编辑器布局和高级题型可视化配置仍未交付。
- Wave 6：套餐、实例、组织连接和平台模板运营页面仍未交付。
- 原生四 runner Release、生产部署与公网 TLS 仍由父计划治理；本地 fresh stack 通过不等于生产验收。
- 专门的屏幕阅读器人工审计、对象存储与多副本网关能力仍是已知边界。

## 4. Task 7 验收证据

- `platform/apps/admin-web/e2e/dashboard.spec.ts` 使用真实短期令牌、真实 PostgreSQL 成员与项目级授权、真实平台
  API 和真实 LimeSurvey 纵向栈，不拦截路由或注入前端 fixture。
- fresh-stack Playwright 15/15 通过；平台 API、平台 DB、gateway 与 engine DB 随后完成一致性核验并归档 E2E 根项目。
- 平台全量 1478 cases / 182 test classes（0 failure / error / skip）；MySQL P1 真实三进程 gate 覆盖首次发布、
  答卷投影、版本 2 重发、版本 1 恢复并发布为版本 3，且历史答卷保持不变。
- 管理端 30 files / 299 tests、lint、typecheck、build（2101 modules）、production-bundle assertion（18 files）、
  Python 管理端 gate 45/45、Dashboard repository focused 16/16、productization 37/37 与能力文档生成/check 均通过。
- 819、820、1179、1180px 均检查页面 `scrollWidth`、可见业务行重叠和主操作边界；819px 另实测刷新、任务、
  问卷和最近工作操作至少 44×44，并逐项点击验证路由及返回 Dashboard。
- 四张截图在全部异步区块和底部“最近工作”稳定后以 `fullPage` 生成，只经现有脱敏和严格文件名、尺寸、路径
  白名单导出。manifest 记录 viewport、document、image 真实尺寸和底部证据，Python gate 解析 PNG IHDR 后验证
  图片宽高与 manifest 一致且覆盖 documentHeight；本轮真实尺寸分别为 819×3236、820×2708、1179×2508、
  1180×2163。人工复核确认 819px 为抽屉导航与单列摘要，820/1179px 为紧凑列表，1180px 为完整表格；
  四个宽度均无可见重叠、横向裁切或主操作缺失。
