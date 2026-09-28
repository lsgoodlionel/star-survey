#!/usr/bin/env bash
# P0-00.8：私有化实例开机自检。
#
# 检查一个已经起来的实例是否满足私有化/气隙交付的硬性条件。
# 覆盖三类来源：
#   A 类  ADR 0002 决定 3/4 的“每租户必须独占”的路径与会话名；
#   B 类  private-deployment.md 里的运行时出网清单；
#   C 类  运行环境本身（PHP 扩展、调试开关、中文 PDF 字体）。
#
# 用法：
#   platform/deploy/private/preflight.sh
#   PREFLIGHT_WEB=xxx PREFLIGHT_DB=yyy platform/deploy/private/preflight.sh
#
# 退出码：0 = 无 FAIL；1 = 存在 FAIL。WARN 不影响退出码，但必须人工处置。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/private/lib.sh"

WEB="${PREFLIGHT_WEB:-$WEB}"
DB="${PREFLIGHT_DB:-$DB}"

FAIL_COUNT=0
WARN_COUNT=0

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; WARN_COUNT=$((WARN_COUNT + 1)); }
section() { printf '\n== %s ==\n' "$1"; }

# 在 web 容器里跑一段 shell，静默失败。
win() { docker exec "$WEB" sh -c "$1" 2>/dev/null; }
# 在 DB 里查一个标量。
dbq() {
  docker exec "$DB" mariadb "-u$DB_USER" "-p$DB_PASSWORD" -N -e "$1" "$DB_NAME" 2>/dev/null \
    | tr -d '[:space:]'
}

# 读实例 config.php 里的一个点分路径键。引擎配置文件开头有
# if (!defined('BASEPATH')) exit(...) 的直访保护，必须先定义 BASEPATH，
# 否则 require 会直接打印那句提示并退出。
read_config() {
  docker exec "$WEB" php -r '
    define("BASEPATH", "/var/www/html");
    $c = require "/var/www/html/application/config/config.php";
    $v = $c;
    foreach (explode(".", $argv[1]) as $k) {
      if (!is_array($v) || !array_key_exists($k, $v)) { echo "unset"; exit; }
      $v = $v[$k];
    }
    echo is_bool($v) ? var_export($v, true) : (string) $v;
  ' "$1" 2>/dev/null
}

# 读一个配置键的“生效值”：实例 config.php 的 config 段优先，
# 没有才回落到 config-defaults.php 的引擎默认值。
read_effective() {
  docker exec "$WEB" php -r '
    define("BASEPATH", "/var/www/html");
    $c = require "/var/www/html/application/config/config.php";
    if (array_key_exists($argv[1], $c["config"] ?? [])) {
      echo (string) $c["config"][$argv[1]];
      exit;
    }
    $config = [];
    require "/var/www/html/application/config/config-defaults.php";
    echo (string) ($config[$argv[1]] ?? "");
  ' "$1" 2>/dev/null
}

if ! docker inspect "$WEB" >/dev/null 2>&1; then
  echo "实例 $WEB 不存在。" >&2
  exit 1
fi

echo "私有化实例自检：$WEB / $DB"

# ---------------------------------------------------------------- A 类
section "A. 实例独占路径与会话（ADR 0002 决定 3、4）"

# 决定 3 列出的五类路径必须来自本实例专属的挂载，不能是共享的绑定挂载。
# 判据：挂载源要么是命名卷（Type=volume），要么根本没被单独挂载（即在
# 容器可写层/独占卷里）。只要出现 Type=bind，就是把宿主目录接进来，
# 多实例共用同一个宿主目录正是 F-01～F-04 的成因。
mounts_json=$(docker inspect "$WEB" --format '{{json .Mounts}}')
shared_bind=$(printf '%s' "$mounts_json" | python3 -c '
import json, sys
for m in json.load(sys.stdin):
    if m.get("Type") == "bind" and m.get("Destination", "").startswith("/var/www/html"):
        print(m["Destination"] + " <- " + m.get("Source", ""))
' 2>/dev/null)
if [[ -z "$shared_bind" ]]; then
  pass "代码树与运行时目录没有宿主绑定挂载（无共享代码树风险）"
else
  fail "存在指向 /var/www/html 的 bind 挂载，可能与邻居实例共享："
  printf '        %s\n' "$shared_bind"
fi

for p in application/config/config.php application/config/allowed_hosts.php; do
  if win "test -f /var/www/html/$p"; then
    pass "$p 存在"
  else
    fail "$p 缺失"
  fi
done

if win 'test -f /var/www/html/application/config/security.php'; then
  pass "security.php 存在（sodium 加密密钥；备份件必须包含它）"
else
  warn "security.php 尚未生成（首次管理员登录时才会创建）；备份前需复查"
fi

session_name=$(read_config 'components.session.sessionName')
if [[ -n "$session_name" && "$session_name" != "PHPSESSID" ]]; then
  pass "会话 cookie 名为 $session_name（不是默认的 PHPSESSID）"
else
  fail "会话 cookie 名是默认值；同域多实例会互相覆盖登录态（ADR 0002 决定 4）"
fi

# ---------------------------------------------------------------- B 类
section "B. 运行时出网通道（见 private-deployment.md 出网清单）"

updatable=$(read_config 'config.updatable')
if [[ "$updatable" == "false" ]]; then
  pass "config.updatable=false（关闭 ComfortUpdate 通知的总开关）"
else
  fail "config.updatable 当前为 $updatable，ComfortUpdate 会在每次后台渲染时外联"
fi

for plugin in UpdateCheck ComfortUpdateChecker; do
  active=$(dbq "SELECT active FROM lime_plugins WHERE name='$plugin'")
  case "$active" in
    0) pass "核心插件 $plugin 已停用" ;;
    "") warn "核心插件 $plugin 不在 lime_plugins 里（未安装或库未就绪）" ;;
    *) fail "核心插件 $plugin 仍处于启用状态（active=$active）" ;;
  esac
done

# UpdateCheck 会为「全部已安装扩展」构造更新器，包括未启用的扩展，
# 所以只停插件不够，必须把唯一一处 <source> 也清掉。
if win 'grep -q "comfortupdate.limesurvey.org" /var/www/html/application/core/plugins/TwoFactorAdminLogin/config.xml'; then
  fail "TwoFactorAdminLogin/config.xml 仍含 <source>comfortupdate.limesurvey.org</source>"
else
  pass "扩展 <source> 更新源已清除"
fi

geonames=$(read_effective 'GeoNamesUsername')
if [[ -z "$geonames" ]]; then
  pass "GeoNamesUsername 为空"
else
  warn "GeoNamesUsername='$geonames'（引擎默认就是 limesurvey）；注意 assets/scripts/map.js 无视该配置，地图题仍会直连 geonames"
fi

if win 'grep -q "openlayers.org" /var/www/html/application/helpers/qanda_helper.php'; then
  warn "qanda_helper.php 仍引用 www.openlayers.org（地图题，无配置开关，需打补丁或禁用地图题）"
else
  pass "openlayers.org 引用已移除"
fi

if win 'grep -q "fonts.googleapis.com" /var/www/html/themes/survey/bootswatch/css/variations/united.min.css'; then
  warn "bootswatch United 变体仍 @import fonts.googleapis.com（使用该主题的问卷页面会外联）"
else
  pass "主题内 Google Fonts @import 已移除"
fi

section "B2. 实际出网探测（判据是探测失败）"
if win 'getent hosts www.limesurvey.org >/dev/null'; then
  fail "容器内可以解析外部域名，网络不是气隙的"
else
  pass "外部域名解析失败（无 DNS 出口）"
fi
if win 'test "$(cat /proc/net/route | awk "NR>1 && \$2==\"00000000\"" | wc -l)" -eq 0'; then
  pass "容器没有默认路由（internal 网络）"
else
  warn "容器有默认路由，具备出网能力；请确认宿主防火墙已拦截出站"
fi

# ---------------------------------------------------------------- C 类
section "C. 运行环境"

# ADR 0001：除 CI 列表外还需要 calendar。
REQUIRED_EXT="gd zip intl ldap mysqli pdo_mysql mbstring xml sodium calendar curl openssl session fileinfo iconv dom"
missing=""
loaded=$(win 'php -m')
for ext in $REQUIRED_EXT; do
  printf '%s\n' "$loaded" | grep -qix "$ext" || missing="$missing $ext"
done
if [[ -z "$missing" ]]; then
  pass "必需 PHP 扩展齐全（含 ADR 0001 追加的 calendar）"
else
  fail "缺少 PHP 扩展：$missing"
fi

if printf '%s\n' "$loaded" | grep -qix xdebug; then
  fail "镜像里带 xdebug，不能用于生产交付"
else
  pass "未加载 xdebug"
fi

debug=$(read_config 'config.debug')
if [[ "$debug" == "0" ]]; then
  pass "debug=0"
else
  fail "debug=$debug，生产必须为 0"
fi

for d in tmp upload; do
  if win "test -w /var/www/html/$d"; then
    pass "$d/ 可写"
  else
    fail "$d/ 不可写"
  fi
done

# 中文 PDF：引擎把 zh-Hans 映射到 cid0cs，而 cid0cs 是 cidfont0（不内嵌字形）。
section "C2. 中文 PDF 导出字体"
if win 'test -f /var/www/html/vendor/tecnickcom/tcpdf/fonts/cid0cs.php'; then
  if win 'head -5 /var/www/html/vendor/tecnickcom/tcpdf/fonts/cid0cs.php | grep -q "cidfont0"'; then
    warn "zh-Hans 使用 cid0cs，类型为 cidfont0：只带度量、不内嵌字形，生成的 PDF 依赖阅读器自备 Adobe-GB1 字体。离线交付若要求 PDF 自包含，须另行嵌入中文 TTF"
  else
    pass "cid0cs 存在且非 cidfont0"
  fi
else
  fail "缺少 zh-Hans 的 PDF 字体 cid0cs"
fi

# ---------------------------------------------------------------- 汇总
printf '\n== 汇总 ==\n  FAIL=%d  WARN=%d\n' "$FAIL_COUNT" "$WARN_COUNT"
[[ "$FAIL_COUNT" -eq 0 ]]
