#!/usr/bin/env bash
# 答卷附件匿名下载防护（P0 发现 11 / ADR 0020）。
#
# 不需要数据库、也不需要已安装的引擎：起一次性容器，往站点根里放探针文件，再从容器内匿名 GET。
# 同一组探针跑三遍——Apache 按镜像现状一遍、把 AllowOverride 关掉再一遍、
# 换 nginx（include 那份守卫片段）再一遍。三遍都必须拒绝，
# 这就是"防护不依赖 .htaccess、也不依赖 Apache"这条不变量。
#
# 镜像**就地重建**到一个本车道自己的标签（默认 survey-web-guard:test），
# 不覆盖别的栈在用的 survey-web:latest。守卫是 Dockerfile 的一部分
# （conf-enabled/zz-engine-static-guard.conf），所以这里测的正是交付物本身。
#
# 想亲眼看修复前的红：把镜像换成不带守卫的那个——
#   GUARD_IMAGE=survey-web platform/deploy/test/run-attachment-guard.sh
#
# 用法：[SURVEY_TEST_PREFIX=<lane>] [GUARD_IMAGE=<tag>] \
#         platform/deploy/test/run-attachment-guard.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_PREFIX="${SURVEY_TEST_PREFIX:-survey}"
GUARD_IMAGE="${GUARD_IMAGE:-survey-web-guard:test}"

if [[ "$GUARD_IMAGE" == "survey-web-guard:test" ]]; then
  echo "building $GUARD_IMAGE from platform/deploy/dev ..."
  docker build -q -t "$GUARD_IMAGE" "$REPO_ROOT/platform/deploy/dev" >/dev/null
fi

python3 "$REPO_ROOT/platform/tests/e2e/attachment_guard.py" \
  --image "$GUARD_IMAGE" \
  --name "$TEST_PREFIX-attachment-guard-probe" \
  --htaccess-root "$REPO_ROOT" \
  --nginx-guard "$REPO_ROOT/platform/deploy/dev/engine-static-guard.nginx.conf"
