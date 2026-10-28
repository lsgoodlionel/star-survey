#!/usr/bin/env bash
# 平台 Java 测试的**标准跑法**：跑前清空 surefire 报告，跑后对账总数、类数与陈旧报告。
#
# 车道汇报「跑了多少条、多少个类」一律走这个脚本，不要手工数 target/surefire-reports。
# 前几波两次被假绿骗到，两次都是人工核对才发现的：
#   1. 新测试类根本没被执行，报告里却 failures=0（靠总数差 14 才发现）；
#   2. 改包名后旧报告残留在 target/surefire-reports 里被重复计数（虚高 14 条 1 个类）。
#
# 用法：
#   PLATFORM_DB_NAME=platform_p3 platform/deploy/test/run-platform-tests.sh
#   PLATFORM_DB_NAME=platform_p3 platform/deploy/test/run-platform-tests.sh --expect-tests 1234
#   PLATFORM_DB_NAME=platform_p3 platform/deploy/test/run-platform-tests.sh -- -Dtest=SurveyServiceTest
#
#   --expect-tests N     与上一轮记录的用例总数对账，对不上就非零退出
#   --expect-classes N   同上，对测试类数
#   --                   之后的参数原样交给 Maven
#
# PLATFORM_MVN 选 Maven 的跑法，默认 platform/deploy/platform-dev/mvn.sh（容器内跑，
# 自己起 platform-db）。CI 上换成 mvn-local.sh：runner 已有 JDK 与 Maven，
# 数据库是 service 容器，连接串由 PLATFORM_DB_URL 给。对账逻辑两条路完全一致——
# 「跑了多少条」的口径只有这一个，不能因为换了执行环境就换一套数法。
#
# 退出码：0 通过；2 对账不通过；其余为 Maven 自己的失败。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MODULE="$REPO_ROOT/platform/services/business"
REPORTS="$MODULE/target/surefire-reports"
SOURCES="$MODULE/src/test/java"
RECONCILE="$REPO_ROOT/platform/tools/test-report/surefire_reconcile.py"
MVN="${PLATFORM_MVN:-$REPO_ROOT/platform/deploy/platform-dev/mvn.sh}"
if [[ ! -x "$MVN" ]]; then
  echo "PLATFORM_MVN 指向的 $MVN 不可执行" >&2
  exit 3
fi

expect_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --expect-tests|--expect-classes)
      [[ $# -ge 2 ]] || { echo "$1 后面要跟一个数字" >&2; exit 3; }
      expect_args+=("$1" "$2")
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "不认识的参数：$1（Maven 参数请放在 -- 之后）" >&2
      exit 3
      ;;
  esac
done

echo "[对账] 数据库：${PLATFORM_DB_URL:-${PLATFORM_DB_NAME:-platform}}"
echo "[对账] Maven：$MVN"

# 跑前清空：陈旧报告只要还在目录里，就一定会被重复计数。
rm -rf "$REPORTS"
if [[ -e "$REPORTS" ]]; then
  echo "[对账] 清不掉报告目录 $REPORTS" >&2
  exit 3
fi

# 一定要 clean：不 clean 的那一轮正是事故 2 的来源（上一轮改包名前的报告留在了 target 里）。
set +e
"$MVN" clean test "$@"
maven_status=$?
set -e
if [[ $maven_status -ne 0 ]]; then
  echo "[对账] Maven 退出码 ${maven_status}；下面的数字只说明跑到哪为止，不代表通过" >&2
fi

set +e
python3 "$RECONCILE" --reports "$REPORTS" --sources "$SOURCES" "${expect_args[@]+"${expect_args[@]}"}"
reconcile_status=$?
set -e

if [[ $maven_status -ne 0 ]]; then
  exit $maven_status
fi
exit $reconcile_status
