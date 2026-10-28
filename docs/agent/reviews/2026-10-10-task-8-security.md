# Dashboard Task 7 安全边界复核

日期：2026-10-11
角色：security
Reviewed range: `be92a0176bca7085831b5c85a2932cf0b3611fa6..e397ecf60ea9b6e449c0750d114a15a309aad436`
Reviewed commit: `e397ecf60ea9b6e449c0750d114a15a309aad436`

## 结论

未发现阻断交付的租户数据泄漏、越权计数、客户端伪造路径、浏览器凭据落盘或截图证据泄密问题。

## 核查范围

- Dashboard repository 新增测试覆盖 preview/export 异常的租户、角色、对象权限、请求人和时间字段边界。
- approver、editor、data 三类短期浏览器凭据只允许测试 actor，文件权限固定为 `0600`，原始值不写入日志或报告。
- 导出证据只接受固定 Dashboard 截图 schema、四个批准宽度和实际 PNG 尺寸；所有角色凭据均参与泄密扫描。
- Playwright 使用真实服务与真实角色授权，未以静态 fixture 替换权限响应，也没有弱化角色断言。
- Harness shallow-history 恢复只接受唯一 40 位 SHA、规范 trusted ref、实际 boundary 精确集合和稳定 ancestry；不存在通用 shallow bypass。

## 证据

- `python3 platform/tests/e2e/test_admin_web_gate.py`：45/45 通过。
- Task 7 完整 Harness 快照：16/16 gate 通过，包括平台 1478 tests、Playwright 15/15、P1、MySQL/PostgreSQL 访问与计分双栈。
- `git diff --check be92a0176..3ec78f0970` 与 `bash -n platform/deploy/test/run-admin-web-e2e.sh` 通过。
- Harness 非 Linux 全套 411/411 通过，浅历史与宿主 schema 负向测试均包含在内。

SPEC_COMPLIANCE=APPROVED
CODE_QUALITY=APPROVED
