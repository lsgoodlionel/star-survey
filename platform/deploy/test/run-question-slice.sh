#!/usr/bin/env bash
# P0-00.3 题型纵切端到端：数组题、自增表格、上传题走完整生命周期
# （发布 → 激活 → 真实 HTTP 作答 → 断点续答 → 改已提交答卷 → 导出 → 重新导入）。
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-question-slice.sh [--fresh]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
is_fresh=false
if [[ "${1:-}" == "--fresh" ]]; then
  is_fresh=true
fi
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/test/lib.sh"

prepare_test_stack
docker exec "$CONTAINER" php platform/tests/e2e/question_slice.php
