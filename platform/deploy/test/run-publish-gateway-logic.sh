#!/usr/bin/env bash
# WP-03 survey logic end to end against the real engine: a definitionVersion 2
# survey (display conditions, a hidden-and-required question, a validation rule,
# calculated values, piped answers) is published through the gateway, the
# engine's own ExpressionManager parses every compiled expression, and scripted
# HTTP respondents check hiding, clearing, validation and stored calculations.
#
# Driver: platform/tests/e2e/publish_gateway_logic.py (host; RemoteControl is
# tunnelled through `docker exec survey-test-web curl`, like run-publish-gateway.sh).
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-gateway-logic.sh [--fresh]
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

python3 "$REPO_ROOT/platform/tests/e2e/publish_gateway_logic.py" \
  --container "$CONTAINER" --db "$TEST_DB"
echo "publish gateway logic e2e passed ($TEST_DB)"
