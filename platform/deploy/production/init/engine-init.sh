#!/bin/sh
set -eu

php_bin="${PHP_BIN:-php}"
console="${ENGINE_CONSOLE:-application/commands/console.php}"

if "$php_bin" "$console" productionInit status; then
    :
else
    status=$?
    if [ "$status" -ne 10 ]; then
        exit "$status"
    fi
    "$php_bin" "$console" productionInit install
fi

"$php_bin" "$console" updatedb
"$php_bin" "$console" productionInit plugins
