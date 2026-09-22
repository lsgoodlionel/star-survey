#!/usr/bin/env bash
# P0-00.8 publish gateway end to end: happy path, rejected definition,
# forced auto-rename with rollback, and post-activation drift detection.
#
# The gateway is Python (platform/tools/publish-gateway) and the engine image
# has no Python, so the test driver runs on the host and tunnels RemoteControl
# through `docker exec survey-test-web curl`.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-gateway.sh [--fresh]
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

python3 "$REPO_ROOT/platform/tests/e2e/publish_gateway.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
