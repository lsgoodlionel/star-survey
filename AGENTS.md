# Survey 的 Codex 工作流约定

- 开发阶段：Codex 可编写或修改 `.github/workflows/`；构建与发布由 Actions 的平台步骤执行。
- 运行阶段：`openai/codex-action` 只做诊断与最小代码修复；计数、提交、推送、报告由 Actions 脚本控制。
- 可手动运行的工作流必须声明 `workflow_dispatch`。自动修复最多 3 次，以提交中的 `[codex-fix-attempt: N]` 为准；仍失败就报告开发者，合并由人工完成。
