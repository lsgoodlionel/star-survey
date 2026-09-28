#!/usr/bin/env bash
# WP-09.1「答案不下发」端到端（契约 survey-exam-v1）：带 exam 块的定义经网关发布到
# 真引擎，再抓作答页的完整 HTML 与它引用的每一个 JS／CSS，断言里面一个正确答案都没有。
#
# 带一组阳性对照：同一个哨兵改用逻辑条件表达时**应当**出现在页面 JS 里
# （引擎会把表达式翻成 JS）。对照组不命中就说明扫描器坏了，此时"没扫到"毫无意义。
#
# Driver: platform/tests/e2e/exam_key.py（宿主机；RemoteControl 与抓取器都通过
# `docker exec <prefix>-test-web` 进容器）。抓取器是 platform/tests/e2e/exam_capture.php。
#
# Usage: [TEST_DB=mysql|pgsql] [SURVEY_TEST_PREFIX=<lane>] \
#          platform/deploy/test/run-exam-key.sh [--fresh]
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

# 单元测试先跑：它们不需要引擎，网关坏了要快速失败。
(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest discover -s tests -t . -q)

python3 "$REPO_ROOT/platform/tests/e2e/exam_key.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "exam key e2e passed ($TEST_DB)"
