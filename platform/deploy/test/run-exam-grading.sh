#!/usr/bin/env bash
# WP-10 客观题自动评分端到端：真引擎作答，服务端按答案键判分，作答者改不了分。
#
# 判分接在 WP-09.1 的答案键之上：答案只存在于 plugin_settings，对错在服务端推出来，
# 成绩写进插件表而不是答卷表——答卷表的每一列都是作答者提交面的一部分。
#
# Driver: platform/tests/e2e/exam_grading.py（宿主机；作答者复用 access_respond.php）。
#
# Usage: [TEST_DB=mysql|pgsql] [SURVEY_TEST_PREFIX=<lane>] \
#          platform/deploy/test/run-exam-grading.sh [--fresh]
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

python3 "$REPO_ROOT/platform/tests/e2e/exam_grading.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "exam grading e2e passed ($TEST_DB)"
