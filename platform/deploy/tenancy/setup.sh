#!/usr/bin/env bash
# P0-00.5：起一套两租户隔离验证栈（各自 DB 容器、DB 账号、tmp 与上传目录）。
#
# 用法：platform/deploy/tenancy/setup.sh
#   租户 A： http://localhost:8091   admin_a / tenant-a-secret
#   租户 B： http://localhost:8092   admin_b / tenant-b-secret
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/tenancy/lib.sh"

prepare_tenancy_stack
echo "租户 A: http://localhost:8091 ($ADMIN_USER_A / $ADMIN_PASSWORD_A)"
echo "租户 B: http://localhost:8092 ($ADMIN_USER_B / $ADMIN_PASSWORD_B)"
