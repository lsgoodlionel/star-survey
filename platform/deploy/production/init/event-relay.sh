#!/bin/sh
# One foreground worker. A heartbeat proves only that the loop is progressing,
# not that the plugin delivered events: plugin cron catches internal errors.
set -u
heartbeat="${SURVEY_RELAY_HEARTBEAT:-/tmp/survey-event-relay-heartbeat}"
trap 'exit 0' TERM INT
while :; do
    if ! php application/commands/console.php plugin cron; then
        echo 'engine event relay invocation failed; will retry' >&2
    fi
    touch "$heartbeat"
    sleep 15 &
    wait "$!" || true
done
