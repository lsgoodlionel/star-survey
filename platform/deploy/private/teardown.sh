#!/usr/bin/env bash
# P0-00.8：拆掉私有化验证栈，连同它的四个卷一起删除。
# 只作用于 survey-private 项目，不碰 dev / test / tenancy / exam 任何一套。
#
# 用法：platform/deploy/private/teardown.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export PRIVATE_EXPOSE=1   # 叠加 expose 层一起 down，避免 edge-net 残留
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/private/lib.sh"

"${COMPOSE[@]}" down -v --remove-orphans
rm -rf "$REPO_ROOT/platform/deploy/private/.runtime"
