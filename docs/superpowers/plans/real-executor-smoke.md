# 真实执行器最小演练

**Status:** approved for explicit human-triggered smoke only

## Milestone smoke-1: 有界 fixture 修改

仅允许把 `tests/fixtures/real_executor_smoke.txt` 从 `baseline` 修改为包含 `smoke-ok` 的预期文本。固定一个 cycle，禁止修改其他路径；认证、配额、权限或工具失败时暂停。
