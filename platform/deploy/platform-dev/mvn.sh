#!/usr/bin/env bash
# 在容器内运行 Maven（本机无需安装 Java）。依赖缓存在命名卷里，重复构建不重新下载。
#   platform/deploy/platform-dev/mvn.sh test
#   PLATFORM_DB_NAME=platform_lane_a platform/deploy/platform-dev/mvn.sh test
#
# PLATFORM_DB_NAME 让并行开发的各条车道各用一个数据库，互不干扰迁移版本与数据；
# 数据库不存在时自动创建（所有者 platform_owner，运行期账号 platform_app 可连接）。
#
# 两个可覆盖项，都是为了「同一份脚本在开发机与 CI 上都能用」：
#   PLATFORM_MAVEN_SETTINGS  settings.xml 的路径。默认是本目录里那份走阿里云镜像的。
#                            置为空字符串则完全不挂 settings.xml，走 Maven 中央仓库——
#                            GitHub runner 在境外，阿里云镜像在那里才是慢的那一头。
#   PLATFORM_MAVEN_REPO      依赖缓存的位置（命名卷或宿主目录）。默认命名卷
#                            platform-maven-repo，各车道共用。想要一个干净的冷缓存来实测
#                            下载耗时，就指向一个临时目录或临时卷，别动共用的那个。
#
# CI 上不走本脚本：runner 由 actions/setup-java 提供 JDK 与 Maven，依赖缓存交给
# actions/cache，数据库用 service 容器。见 mvn-local.sh 与 .github/workflows/platform-quality.yml。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
DB_NAME="${PLATFORM_DB_NAME:-platform}"
if [[ ! "$DB_NAME" =~ ^[a-z][a-z0-9_]{0,40}$ ]]; then
  echo "invalid PLATFORM_DB_NAME: $DB_NAME" >&2
  exit 2
fi
PLATFORM_DB_NAME="$DB_NAME" "$HERE/ensure-platform-db.sh"

mounts=(
  -v "$REPO_ROOT/platform/services/business:/workspace"
  -v "${PLATFORM_MAVEN_REPO:-platform-maven-repo}:/root/.m2/repository"
)
# 显式置空＝不要任何 settings.xml（Maven 中央仓库）。未设置时用本目录那份镜像配置。
settings="${PLATFORM_MAVEN_SETTINGS-$HERE/maven-settings.xml}"
if [[ -n "$settings" ]]; then
  mounts+=(-v "$settings:/root/.m2/settings.xml:ro")
fi

docker run --rm \
  --network platform-dev_default \
  "${mounts[@]}" \
  -w /workspace \
  -e PLATFORM_DB_URL="jdbc:postgresql://platform-db:5432/$DB_NAME" \
  maven:3.9-eclipse-temurin-21 \
  mvn -B -q "$@"
