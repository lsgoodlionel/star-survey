#!/usr/bin/env bash
# P0-00.5：起栈并从租户 A 的实例内部穷举越权访问租户 B。
# 任何一次越权成功都会让脚本以非零码退出。
#
# 用法：platform/deploy/tenancy/run-isolation.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/tenancy/lib.sh"

prepare_tenancy_stack
docker exec "$WEB_A" php platform/tests/e2e/tenancy_isolation.php
