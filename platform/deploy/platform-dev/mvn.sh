#!/usr/bin/env bash
# 在容器内运行 Maven（本机无需安装 Java）。依赖缓存在命名卷里，重复构建不重新下载。
#   platform/deploy/platform-dev/mvn.sh test
#   PLATFORM_DB_NAME=platform_lane_a platform/deploy/platform-dev/mvn.sh test
#
# PLATFORM_DB_NAME 让并行开发的各条车道各用一个数据库，互不干扰迁移版本与数据；
# 数据库不存在时自动创建（所有者 platform_owner，运行期账号 platform_app 可连接）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
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

docker run --rm \
  --network platform-dev_default \
  -v "$REPO_ROOT/platform/services/business:/workspace" \
  -v "$HERE/maven-settings.xml:/root/.m2/settings.xml:ro" \
  -v platform-maven-repo:/root/.m2/repository \
  -w /workspace \
  -e PLATFORM_DB_URL="jdbc:postgresql://platform-db:5432/$DB_NAME" \
  maven:3.9-eclipse-temurin-21 \
  mvn -B -q "$@"
