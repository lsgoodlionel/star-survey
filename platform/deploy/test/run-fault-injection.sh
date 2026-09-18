#!/usr/bin/env bash
# P0-00.4 end-to-end fault injection: real HTTP survey runtime, worker killed
# at chosen engine events, event log checked before and after the cron scan.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-fault-injection.sh [--fresh]
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
docker exec "$CONTAINER" php platform/tests/e2e/fault_injection.php
