#!/usr/bin/env bash
# WP-03.4 双执行比对 end to end: a definitionVersion 2 survey with a scoring table
# is published through the gateway, then every expression in it is evaluated twice —
# once by the platform's own DSL interpreter and once by the real LimeSurvey
# ExpressionManager running the compiled ExpressionScript — over the same answer
# vectors. Each case carries a hand-written expected value so a divergence names
# the side that is wrong. Two real HTTP respondents anchor the comparison against
# the scores the engine actually stores in the response table.
#
# Driver: platform/tests/e2e/publish_gateway_parity.py (host; RemoteControl is
# tunnelled through `docker exec <prefix>-test-web curl`, like run-publish-gateway.sh).
#
# 这是**固定快照**，接 CI 并没有改变这一点。答案向量与期望值全部写死在
# platform/tests/fixtures/surveys/publish-gateway-scoring.vectors.json 里：4 条手写向量
# × 定义里 12 处表达式 = 48 条用例，期望值按 survey-logic-dsl-v1.md 人工写下、
# 刻意不从任何一侧生成（否则分歧时没有裁判）。所以它证明的是「这 48 条上两侧一致」，
# 不是「DSL 与 ExpressionManager 处处等价」；覆盖面要靠往 vectors.json 里加向量来扩。
# 接 CI 的意义只有一个：这份快照从此每次改动都被重跑，而不是想起来才跑一次。
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-gateway-parity.sh [--fresh]
#   CI: .github/workflows/platform-quality.yml 的 gateway-parity-e2e（mysql/pgsql 两个矩阵分支）
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

# Unit tests first: they need no engine, so a broken gateway fails fast.
(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest discover -s tests -t .)

python3 "$REPO_ROOT/platform/tests/e2e/publish_gateway_parity.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "publish gateway scoring parity e2e passed ($TEST_DB)"
