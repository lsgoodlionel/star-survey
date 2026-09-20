#!/usr/bin/env bash
# P0-00.6 publishing round trip: LSS coverage, fieldmap mapping stability,
# post-activation drift surface, and activation failure cleanup.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-roundtrip.sh [--fresh]
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
docker exec "$CONTAINER" php platform/tests/e2e/publish_roundtrip.php
