#!/usr/bin/env bash
# 拆掉 survey-functional 栈（容器＋命名卷）。日志留在 .runtime/logs/ 里。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
docker compose -f "$REPO_ROOT/platform/deploy/functional/docker-compose.functional.yml" \
  down -v --remove-orphans
