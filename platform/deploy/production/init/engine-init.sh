#!/bin/sh
set -eu

php_bin="${PHP_BIN:-php}"
console="${ENGINE_CONSOLE:-application/commands/console.php}"

if "$php_bin" "$console" productionInit status; then
    exit 0
else
    status=$?
    case "$status" in
        10) "$php_bin" "$console" productionInit install ;;
        20) echo "recovering incomplete production initialization" >&2 ;;
        *) exit "$status" ;;
    esac
fi

"$php_bin" "$console" updatedb
"$php_bin" "$console" productionInit admin
"$php_bin" "$console" productionInit settings
"$php_bin" "$console" productionInit plugins
"$php_bin" "$console" productionInit complete
