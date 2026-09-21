#!/usr/bin/env bash
# 重新抓取金样请求：用任一带 PHP 8 + curl 的镜像，运行仓库里真实的 MjyEventRelay + MjyHttpEventTransport，
# 发往容器内的抓包服务器，把请求头与请求体写回 engine-contract/。PHP 发送端改动后运行一次并提交结果，
# PhpCapturedRequestTest 会据此验证平台仍接受真实请求。
# MASTER_SECRET 必须与 SignedEventRequests.MASTER_SECRET 相同；driver.php 在 PHP 里由它派生实例密钥，
# 交给真实的发送端签名（请求头带 X-Mjy-Engine-Instance）。
#   platform/services/business/src/test/resources/engine-contract/capture/regenerate.sh [php-image]
# 默认镜像 survey-web:latest（本仓库的引擎镜像，自带 PHP 8.3）。只启动一次性容器，不碰运行中的容器。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../../../../../.." && pwd)"
IMAGE="${1:-survey-web:latest}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

docker run --rm --entrypoint sh \
  -v "$HERE:/h:ro" \
  -v "$OUT:/out" \
  -v "$REPO_ROOT/plugins/MjyPlatformBridge:/plugins:ro" \
  -e ENDPOINT=http://127.0.0.1:8099/internal/engine-events \
  -e MASTER_SECRET=test-only-engine-events-secret-at-least-32-bytes \
  -e INSTANCE=hd/engine-01 \
  "$IMAGE" /h/run.sh

cp "$OUT/body.bin" "$HERE/../php-relay-request.body"
cp "$OUT/headers.txt" "$HERE/../php-relay-request.headers"
echo "captured $(wc -c < "$HERE/../php-relay-request.body") bytes"
