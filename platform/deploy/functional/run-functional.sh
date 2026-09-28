#!/usr/bin/env bash
# P0-00.2：在隔离的 survey-functional 栈里跑引擎的浏览器套件。
#
# 用法：
#   platform/deploy/functional/run-functional.sh [--fresh] <suite> [suite...]
#   platform/deploy/functional/run-functional.sh --raw [phpunit 参数...]
#
#   --fresh   跑之前先销毁数据库与 tmp 卷，从零安装
#   --raw     把后面的参数原样交给 phpunit（调试单个用例时用）
#   --with-mjy-plugins  跑之前激活 plugins/Mjy*（归因对照用：和基线比对结果是否变化）
#
# 每个套件单独一次 phpunit，日志写到 platform/deploy/functional/.runtime/logs/。
# 与 CI 不同的是这里不加 --stop-on-failure：我们要的是真实通过率，不是第一个红灯。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
is_fresh=false
is_raw=false
with_mjy_plugins=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh) is_fresh=true; shift ;;
    --raw) is_raw=true; shift; break ;;
    --with-mjy-plugins) with_mjy_plugins=true; shift ;;
    *) break ;;
  esac
done

# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/functional/lib.sh"

LOG_DIR="$FUNCTIONAL_DIR/.runtime/logs"
# 归因对照的日志单独放一层，方便和基线逐个套件对比。
if $with_mjy_plugins; then
  LOG_DIR="$LOG_DIR/with-mjy-plugins"
fi
mkdir -p "$LOG_DIR"

# CI 里每个套件是一个独立 job（全新的库与检出）。本地要复现这一点：
# InstallationControllerTest 会 DROP DATABASE 并删掉 config.php，跑完之后
# 同一个栈已经不适合再跑下一个套件了。所以每个套件之前都重新 prepare 一次。
prepare_suite() {
  prepare_functional_stack
  if $with_mjy_plugins; then
    activate_mjy_plugins
  else
    # 基线：确保上一轮归因对照留下的记录不会污染这一轮。
    deactivate_mjy_plugins
  fi
}

# phpunit 在 web 容器里跑：DOMAIN 指向浏览器看得见的站点，WEBDRIVERHOST 指向
# WebDriver。DBLOCATION/DBUSER/DBPASSWORD 供 InstallationControllerTest 填安装
# 向导的表单用——CI 的库在 localhost，我们的库在独立容器 db 上。
run_phpunit() {
  docker exec \
    -e DOMAIN="$DOMAIN" \
    -e WEBDRIVERHOST="$WEBDRIVER_HOST" \
    -e WEBDRIVERSUFFIX="$WEBDRIVER_SUFFIX" \
    -e DBLOCATION=db \
    -e DBUSER=root \
    -e DBPASSWORD=root \
    "$CONTAINER" php -d memory_limit=2G vendor/bin/phpunit "$@"
}

if $is_raw; then
  prepare_suite
  run_phpunit "$@"
  exit $?
fi

if [[ $# -eq 0 ]]; then
  echo "用法: $0 [--fresh] <suite> [suite...]   例如: $0 security api" >&2
  exit 2
fi

overall_status=0
for suite in "$@"; do
  log_file="$LOG_DIR/$suite.log"
  echo "===== testsuite: $suite ====="
  prepare_suite
  started=$(date +%s)
  # 单个套件失败不能中断整轮，最后统一汇报。
  if run_phpunit --testdox --testsuite "$suite" 2>&1 | tee "$log_file"; then
    suite_status=0
  else
    suite_status=1
    overall_status=1
  fi
  elapsed=$(( $(date +%s) - started ))
  echo "----- $suite 结束，用时 ${elapsed}s，状态 $suite_status（日志：$log_file）"
done

exit "$overall_status"
