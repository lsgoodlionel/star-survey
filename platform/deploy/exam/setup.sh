#!/usr/bin/env bash
# P0-00.7：起一套独立的考试与配额验证栈。
#
# 用法：platform/deploy/exam/setup.sh
#   http://localhost:8093   admin / exam-secret
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/exam/lib.sh"

prepare_exam_stack
echo "考试验证栈: http://localhost:8093 ($ADMIN_USER / $ADMIN_PASSWORD)"
