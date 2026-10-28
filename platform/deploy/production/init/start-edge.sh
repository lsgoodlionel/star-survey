#!/bin/sh
set -eu

hash_file="${ENGINE_ADMIN_PASSWORD_HASH_FILE:-/run/secrets/engine_admin_password_hash}"
if [ ! -s "$hash_file" ]; then
    echo "engine-admin access policy is not configured" >&2
    exit 1
fi

ENGINE_ADMIN_PASSWORD_HASH="$(cat "$hash_file")"
export ENGINE_ADMIN_PASSWORD_HASH
exec "$@"
