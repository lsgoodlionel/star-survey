"""考试答案键（WP-09.1，契约 survey-exam-v1）。

定义里的 ``exam`` 块在这里校验（``schema``）、编译成一行插件载荷（``compile``），
并在发布期挡住会把答案送进浏览器的两条通道（``disclosure``）。判分在引擎插件
MjyRuntimePolicy 里执行，网关只负责把答案键可核对地送到、且**只送到插件**。
"""
