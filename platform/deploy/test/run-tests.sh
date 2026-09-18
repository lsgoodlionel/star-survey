#!/usr/bin/env bash
# Run the engine phpunit suites in an isolated stack that mirrors CI
# (.github/workflows): root/root DB, admin/password, debug=0.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-tests.sh [--fresh] [phpunit args...]
#   TEST_DB   mysql (MariaDB 10.11, default) or pgsql (PostgreSQL 16)
#   --fresh   recreate the test database before running
#   default   --testsuite unit (pgsql adds --exclude-group mysql, as CI does)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
is_fresh=false
if [[ "${1:-}" == "--fresh" ]]; then
  is_fresh=true
  shift
fi
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/test/lib.sh"

phpunit_args=("$@")
if [[ ${#phpunit_args[@]} -eq 0 ]]; then
  phpunit_args=(--testsuite unit)
  if [[ "$TEST_DB" == "pgsql" ]]; then
    phpunit_args+=(--exclude-group mysql)
  fi
fi

prepare_test_stack
docker exec "$CONTAINER" php -d memory_limit=2G vendor/bin/phpunit "${phpunit_args[@]}"
