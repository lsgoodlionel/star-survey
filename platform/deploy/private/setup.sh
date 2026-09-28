#!/usr/bin/env bash
# P0-00.8：起一套私有化最小单节点栈（Web + MariaDB，独占代码卷，内网 internal 网络）。
#
# 用法：
#   platform/deploy/private/setup.sh            # 纯内网（无网关，容器出不了网）
#   platform/deploy/private/setup.sh --expose   # 额外映射宿主 8094（会恢复出网能力）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
if [[ "${1:-}" == "--expose" ]]; then
  export PRIVATE_EXPOSE=1
fi
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/private/lib.sh"

prepare_private_stack

if [[ "${PRIVATE_EXPOSE:-0}" == "1" ]]; then
  echo "实例： http://localhost:$WEB_PORT  ($ADMIN_USER / $ADMIN_PASSWORD)"
  echo "注意：--expose 让 web 接入带网关的 edge-net，internal 网络的气隙保证已失效。"
else
  echo "实例运行在纯内网上，未映射宿主端口。"
  echo "访问方式： docker exec $WEB curl -sS http://localhost/index.php/admin"
fi
