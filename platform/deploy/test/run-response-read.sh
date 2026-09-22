#!/usr/bin/env bash
# WP-06 slice 06.1: the gateway response-read endpoint against a real engine.
# Verifies that export_responses (json, code headings) emits columns in the
# requested order, so the gateway can map values back to engine fieldnames.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-response-read.sh [--fresh]
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

(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest tests.test_responses)

python3 "$REPO_ROOT/platform/tests/e2e/response_read.py" --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
