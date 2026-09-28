#!/usr/bin/env bash
# WP-09.2 服务端计时与强制交卷端到端（ADR 0007 的两项遗留）：到点由服务端收卷，
# 剩余时间由服务端下发，断线可以续考。含四类篡改：改客户端时钟、重放旧会话、
# 不进场直接 POST、伪造交卷时间字段。
#
# Driver: platform/tests/e2e/exam_timing.py（宿主机；作答者复用 access_respond.php）。
# 整轮约一分半：中间要真等服务端的考试时间走完。
#
# Usage: [TEST_DB=mysql|pgsql] [SURVEY_TEST_PREFIX=<lane>] \
#          platform/deploy/test/run-exam-timing.sh [--fresh]
# Set COMPOSE_PROJECT_NAME to the same value as SURVEY_TEST_PREFIX when running
# next to other lanes.
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
enable_remote_control
db_query "DELETE FROM lime_plugins WHERE name = 'MjyRuntimePolicy'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyRuntimePolicy', 'user', 1, 0, '0.1.0', 0)" >/dev/null
docker exec "$CONTAINER" rm -rf tmp/runtime/cache

python3 "$REPO_ROOT/platform/tests/e2e/exam_timing.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "exam timing e2e passed ($TEST_DB)"
