#!/usr/bin/env bash
# P0-00.7：彻底拆除考试与配额验证栈（含数据卷）。
#
# 用法：platform/deploy/exam/teardown.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/exam/lib.sh"

"${COMPOSE[@]}" down -v
rm -rf "$EXAM_DIR/.runtime"
echo "考试验证栈已拆除。"
