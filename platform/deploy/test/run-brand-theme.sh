#!/usr/bin/env bash
# WP-19 slice 19.3 brand theme + multi-language end to end against the real engine
# (contract platform/contracts/survey-branding-v1.md): the zh-business theme is
# installed into the engine database, a branded two-language definition is published
# through the gateway, and the rendered respondent page is checked for the brand, for
# the language switch and its fallback, for escaping of injected markup, and for the
# absence of any third-party origin or upstream branding.
#
# Driver: platform/tests/e2e/brand_theme.py (host; RemoteControl and the respondent
# page are tunnelled through `docker exec <prefix>-test-web curl`).
#
# Usage: [TEST_DB=mysql|pgsql] [SURVEY_TEST_PREFIX=<lane>] \
#          platform/deploy/test/run-brand-theme.sh [--fresh]
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

# Unit tests first: they need no engine, so a broken gateway fails fast.
(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest discover -s tests -t .)

python3 "$REPO_ROOT/platform/tests/e2e/brand_theme.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "brand theme e2e passed ($TEST_DB)"
