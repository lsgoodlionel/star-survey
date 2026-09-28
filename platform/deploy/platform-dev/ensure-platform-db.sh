#!/usr/bin/env bash
# 确保 platform-db 在跑、且 $PLATFORM_DB_NAME 那个库存在。幂等，可以反复调。
#
#   PLATFORM_DB_NAME=platform_lane_a platform/deploy/platform-dev/ensure-platform-db.sh
#
# 原先这段逻辑只长在 mvn.sh 里，于是 run-p1-e2e.sh 靠「调了一次 mvn.sh，数据库就顺便起来了」
# 这个副作用活着——一旦它改用别的方式跑 Maven（CI 上就是），紧接着的 psql 会对着一个
# 不存在的容器执行。抽出来是为了把这个隐式依赖变成显式的一行。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_NAME="${PLATFORM_DB_NAME:-platform}"
if [[ ! "$DB_NAME" =~ ^[a-z][a-z0-9_]{0,40}$ ]]; then
  echo "invalid PLATFORM_DB_NAME: $DB_NAME" >&2
  exit 2
fi
COMPOSE=(docker compose -f "$HERE/docker-compose.platform.yml" -p platform-dev)

# 已在运行且健康就直接复用：compose 会把另一个工作树里的挂载路径视为配置变化而重建容器，
# 重建会断开所有并行车道的连接，并让挂在该网络上的其他容器（如端到端的平台容器）解析不到 platform-db。
if [[ "$(docker inspect -f '{{.State.Health.Status}}' platform-db 2>/dev/null)" != "healthy" ]]; then
  "${COMPOSE[@]}" up -d platform-db >/dev/null
fi
until [[ "$(docker inspect -f '{{.State.Health.Status}}' platform-db)" == "healthy" ]]; do
  sleep 2
done

exists=$(docker exec platform-db psql -U platform_owner -d platform -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")
if [[ "$exists" != "1" ]]; then
  docker exec platform-db psql -U platform_owner -d platform -qc "CREATE DATABASE $DB_NAME OWNER platform_owner"
  docker exec platform-db psql -U platform_owner -d platform -qc "GRANT CONNECT ON DATABASE $DB_NAME TO platform_app"
fi
