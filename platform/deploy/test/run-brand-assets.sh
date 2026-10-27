#!/usr/bin/env bash
# Wave2 Task2 only. Never sources lib.sh or modifies the existing test stack.
# Usage: bash platform/deploy/test/run-brand-assets.sh [--db mysql|pgsql|all]
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
python3 -m unittest discover -s "$REPO_ROOT/platform/tests/e2e" -p test_brand_assets.py -v
python3 "$REPO_ROOT/platform/tests/e2e/brand_assets.py" "$@"
