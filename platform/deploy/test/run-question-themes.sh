#!/usr/bin/env bash
# WP-02 slice 02.3 end to end against the real engine: the question types that needed
# a new engine theme (R02-04/07/14/28/43) and the two that project into the plugin
# side tables (R02-13 repeating table, R02-19 heatmap).
#
# The mjy-* question themes are installed into lime_question_themes first (a missing
# theme is silently downgraded by the engine, ADR 0006 decision 6), then
# platform/tests/fixtures/surveys/question-themes.json is published through the
# gateway, every page is smoke-rendered over HTTP (the theme markup and its JS must
# actually appear), the survey is answered with edge-case values, every stored column
# and every projected side-table cell is checked in the database together with its
# structure version, the values are read back through the gateway response-read
# service, and tampered submissions must keep the respondent on the page.
#
# Driver: platform/tests/e2e/question_themes.py (host; RemoteControl is tunnelled
# through `docker exec <prefix>-test-web curl`, like run-question-types.sh).
#
# Usage: [SURVEY_TEST_PREFIX=lane] [TEST_DB=mysql|pgsql] platform/deploy/test/run-question-themes.sh [--fresh]
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

# The side-table question types need the plugin: it is the only server-side gate
# for the JSON envelope, and the only thing that writes the side tables.
db_query "DELETE FROM lime_plugins WHERE name = 'MjyQuestionExtensions'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyQuestionExtensions', 'user', 1, 0, '0.1.0', 0)" >/dev/null
docker exec "$CONTAINER" rm -rf tmp/runtime/cache

# Unit tests first: they need no engine, so a broken gateway fails fast.
(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest discover -s tests -t .)

python3 "$REPO_ROOT/platform/tests/e2e/question_themes.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "question themes e2e passed ($TEST_DB)"
