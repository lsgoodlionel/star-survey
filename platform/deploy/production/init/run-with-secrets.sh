#!/bin/sh
set -eu

while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    name="${1%%=*}"
    file="${1#*=}"
    if [ "$name" = "$file" ] || [ ! -s "$file" ]; then
        echo "required secret file is missing for $name" >&2
        exit 1
    fi
    value="$(cat "$file")"
    export "$name=$value"
    shift
done

[ "${1:-}" = "--" ] || { echo "secret mapping terminator is missing" >&2; exit 1; }
shift
[ "$#" -gt 0 ] || { echo "runtime command is missing" >&2; exit 1; }
exec "$@"
