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
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-gateway-parity.sh [--fresh]
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
