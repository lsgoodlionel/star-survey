#!/usr/bin/env bash
# RemoteControl 写答卷接口的旁路与闸门（P0 发现 12、21；ADR 0007 第 3 条、ADR 0021）。
#
# 先跑网关单测（不需要引擎，白名单坏了就快速失败），再在真引擎上跑
# platform/tests/e2e/rpc_gate.py：窗口已截止的问卷上，正常作答被拒而
# RemoteControl add_response / update_response 也必须被拒；同时验证闸门
# 不破坏发布网关的既有能力（发布、回读、收口、导出、插件通道）。
#
# 想亲眼看修复前的红：把 MjyRuntimePolicy::init() 里
#   $this->subscribe('beforeControllerAction');
# 那一行去掉（这就是修复前的插件），再跑
#   RPC_GATE_ONLY_BYPASS=1 platform/deploy/test/run-rpc-gate.sh
# B/C 会报「答卷表里仍然没有已提交的答卷」与「库里的答案原样未动」失败——旁路成立。
# 不能用"停用插件"来复现：窗口策略正是靠这个插件执行的，停用后带策略的发布会回滚。
#
# 用法：[TEST_DB=mysql|pgsql] [SURVEY_TEST_PREFIX=<lane>] \
#         platform/deploy/test/run-rpc-gate.sh [--fresh]
# 与别的车道并行时把 COMPOSE_PROJECT_NAME 设成与 SURVEY_TEST_PREFIX 相同的值。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
is_fresh=false
if [[ "${1:-}" == "--fresh" ]]; then
  is_fresh=true
fi
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/test/lib.sh"

prepare_test_stack
enable_remote_control
db_query "DELETE FROM lime_plugins WHERE name = 'MjyRuntimePolicy'" >/dev/null
db_query "INSERT INTO lime_plugins (name, plugin_type, active, priority, version, load_error)
          VALUES ('MjyRuntimePolicy', 'user', 1, 0, '0.1.0', 0)" >/dev/null
docker exec "$CONTAINER" rm -rf tmp/runtime/cache

# 网关单测先行：不需要引擎，白名单坏了就快速失败。
(cd "$REPO_ROOT/platform/tools/publish-gateway" && python3 -m unittest discover -s tests -t . -q)

extra=()
if [[ "${RPC_GATE_ONLY_BYPASS:-}" == "1" ]]; then
  extra+=(--only-bypass)
fi

python3 "$REPO_ROOT/platform/tests/e2e/rpc_gate.py" \
  --container "$CONTAINER" --db "$TEST_DB" --db-container "$TEST_PREFIX-$DB_SERVICE" "${extra[@]+"${extra[@]}"}"
echo "rpc gate e2e passed ($TEST_DB)"
