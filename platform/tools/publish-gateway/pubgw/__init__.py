"""平台问卷定义 → LimeSurvey 引擎的发布网关（原型）。

链路：校验 → 编译 LSS → 导入 → 补发 LSS 带不动的东西 → 激活 → 回读校验 →
失败回滚 → 产出绑定记录 → 周期性漂移检查。证据与决定见
platform/docs/adr/0009-publish-gateway.md 与 platform/docs/adr/0005-publishing.md。
"""

import logging

__all__ = ["GATEWAY_VERSION"]

# 库本身不配置日志输出；服务入口（server.main）负责配置根日志。
logging.getLogger("pubgw").addHandler(logging.NullHandler())

GATEWAY_VERSION = "0.1.0"
