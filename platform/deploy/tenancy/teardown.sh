#!/usr/bin/env bash
# P0-00.5：拆掉租户隔离验证栈，连同它的数据卷一起删除。
# 只作用于 survey-tenancy 项目，不碰 dev 与 test 栈。
#
# 用法：platform/deploy/tenancy/teardown.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/tenancy/lib.sh"

"${COMPOSE[@]}" down -v --remove-orphans
rm -rf "$REPO_ROOT/platform/deploy/tenancy/.runtime"
