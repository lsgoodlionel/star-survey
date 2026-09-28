#!/usr/bin/env bash
# P0-00.7 端到端策略验证：真实 HTTP 填写、超时拒绝、最后名额并发争抢。
#
# 用法：[EXAM_CONCURRENCY=100] platform/deploy/exam/run-exam-policy.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/exam/lib.sh"

prepare_exam_stack
docker exec -e EXAM_CONCURRENCY="${EXAM_CONCURRENCY:-100}" "$EXAM_WEB" \
  php platform/tests/e2e/exam_policy.php
