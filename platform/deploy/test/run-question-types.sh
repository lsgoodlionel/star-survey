#!/usr/bin/env bash
# WP-02 slices 02.1-02.2 end to end against the real engine: a survey holding every
# question type of the batch (platform/tests/fixtures/surveys/question-types.json) is
# published through the gateway, answered over HTTP, every stored column is checked
# in the database and read back through the gateway response-read service, and
# tampered submissions (invalid codes, ranges, Chinese format checksums, forged
# relevance*/java* shadow fields) must keep the respondent on the page.
#
# Driver: platform/tests/e2e/question_types.py (host; RemoteControl is tunnelled
# through `docker exec <prefix>-test-web curl`, like run-publish-gateway.sh).
#
# Usage: [SURVEY_TEST_PREFIX=lane] [TEST_DB=mysql|pgsql] platform/deploy/test/run-question-types.sh [--fresh]
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

python3 "$REPO_ROOT/platform/tests/e2e/question_types.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE"
echo "question types e2e passed ($TEST_DB)"
