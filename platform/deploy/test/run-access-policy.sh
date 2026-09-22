#!/usr/bin/env bash
# WP-04.1 / 04.2 access policy end to end against the real engine (ADR 0016):
# definitions with a policy block are published through the gateway (the gateway
# reads the policy back from MjyRuntimePolicy before activating), then scripted
# HTTP respondents check windows (incl. a changed client clock), the access
# password, per-device / per-token limits, the server-side time limit, captcha and
# IP rules. The plugin-missing case must roll the publish back.
#
# Driver: platform/tests/e2e/access_policy.py (host; RemoteControl and the plugin
# status endpoint are tunnelled through `docker exec <prefix>-test-web curl`).
# Takes about two minutes: two scenarios wait for server-side time to run out.
#
# Usage: [TEST_DB=mysql|pgsql] [SURVEY_TEST_PREFIX=<lane>] \
#          platform/deploy/test/run-access-policy.sh [--fresh]
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

# Unit tests first: they need no engine, so a broken gateway fails fast.
(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest discover -s tests -t .)

python3 "$REPO_ROOT/platform/tests/e2e/access_policy.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "access policy e2e passed ($TEST_DB)"
