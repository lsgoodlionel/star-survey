#!/usr/bin/env bash
# 用**本机安装的** Maven 跑，不起容器、不碰数据库——数据库由调用方备好，
# 连接串通过 PLATFORM_DB_URL 传进来。
#
# 这是 CI 走的那条路：runner 上 actions/setup-java 已经装好 JDK 21 与 Maven，
# 依赖缓存交给 actions/cache（缓存命中时 ~10 秒，比在容器里重新解析快得多），
# 数据库用 service 容器。没有 settings.xml，因此走 Maven 中央仓库——
# 阿里云镜像是为国内开发机准备的，在境外 runner 上它才是慢且易抖的那一头。
#
# 用法（与 mvn.sh 同形，供 run-platform-tests.sh 通过 PLATFORM_MVN 选用）：
#   PLATFORM_MVN=platform/deploy/platform-dev/mvn-local.sh \
#   PLATFORM_DB_URL=jdbc:postgresql://localhost:5432/platform_ci \
#     platform/deploy/test/run-platform-tests.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"

if ! command -v mvn >/dev/null 2>&1; then
  echo "本机没有 mvn。开发机请用 platform/deploy/platform-dev/mvn.sh（容器内跑）。" >&2
  exit 3
fi

# 刻意**不**校验 PLATFORM_DB_URL。这里一度加过「没设就退 3」的闸门，它是错的：
# `-DskipTests package` 只编译打包，一行 SQL 都不跑（pom 里没有绑定到构建生命周期的
# flyway 插件，迁移是运行期 Spring Boot 做的）。而 run-p1-e2e.sh 正是先 package 出 jar、
# 再把连接串通过 `docker run -e` 交给平台容器——宿主 shell 里本来就没有这个变量。
# 那道闸门会让 p1-e2e 在构建这一步就退出。
# 数据库归调用方管：需要连库的目标（test）没设连接串时，Spring 自己会报得很清楚。

cd "$REPO_ROOT/platform/services/business"
exec mvn -B -q "$@"
