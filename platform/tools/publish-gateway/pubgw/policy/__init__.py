"""访问与作答规则（WP-04.1 / 04.2，ADR 0016，契约 survey-access-policy-v1）。

定义里的 ``policy`` 块在这里校验（``schema``）、编译成原生设置与插件载荷（``compile``），
发布时由插件状态端点回读核对（``probe``）。判定本身在引擎插件 MjyRuntimePolicy 里执行，
网关只负责把规则原样、可核对地送到。
"""
