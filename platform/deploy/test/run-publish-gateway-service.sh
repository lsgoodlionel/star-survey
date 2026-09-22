#!/usr/bin/env bash
# Publish gateway as an HTTP service (contract platform/contracts/publish-gateway-v1.md),
# end to end against the real engine test stack:
#
#   host driver --HTTP+HMAC--> survey-test-pubgw (container) --RemoteControl--> survey-test-web
#
# The gateway container joins the test stack's network and reaches the engine by
# container name, exactly as the platform will in production; only the gateway
# port is published, on 127.0.0.1, for the driver.
#
# Asserts: /healthz, 401 unsigned, 404 unknown instance, 200 published with an
# active survey in the database, idempotent replay by requestId (no second survey),
# and that no response contains the engine password.
#
# Usage: [TEST_DB=mysql|pgsql] platform/deploy/test/run-publish-gateway-service.sh [--fresh]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEST_DB="${TEST_DB:-mysql}"
is_fresh=false
if [[ "${1:-}" == "--fresh" ]]; then
  is_fresh=true
fi
# shellcheck source=lib.sh
source "$REPO_ROOT/platform/deploy/test/lib.sh"

GATEWAY_DIR="$REPO_ROOT/platform/tools/publish-gateway"
GATEWAY_IMAGE=survey-publish-gateway:test
GATEWAY_CONTAINER=survey-test-pubgw
INSTANCE_ID=survey-test-web
ENGINE_PASSWORD_ENV=PUBGW_ENGINE_TEST_PASSWORD
DEFINITION="$REPO_ROOT/platform/tests/fixtures/surveys/publish-gateway.json"
HEALTH_ATTEMPTS=30

cleanup() {
  docker rm -f "$GATEWAY_CONTAINER" >/dev/null 2>&1 || true
  if [[ -n "${WORK_DIR:-}" ]]; then
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

prepare_test_stack
enable_remote_control

# Unit tests first: they need no engine, so a broken gateway fails fast.
(cd "$GATEWAY_DIR" && python3 -m unittest discover -s tests -t .)

docker build -q -t "$GATEWAY_IMAGE" "$GATEWAY_DIR" >/dev/null

NETWORK="$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' "$CONTAINER")"
SHARED_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
WORK_DIR="$(mktemp -d)"
cat >"$WORK_DIR/engines.json" <<JSON
{
  "$INSTANCE_ID": {
    "rpcUrl": "http://$CONTAINER/index.php/admin/remotecontrol",
    "user": "$ADMIN_USER",
    "passwordEnv": "$ENGINE_PASSWORD_ENV"
  }
}
JSON

# create + cp + start: the config goes in without a host bind mount.
docker rm -f "$GATEWAY_CONTAINER" >/dev/null 2>&1 || true
docker create --name "$GATEWAY_CONTAINER" --network "$NETWORK" -p 127.0.0.1::8080 \
  -e PUBGW_SHARED_SECRET="$SHARED_SECRET" \
  -e PUBGW_ENGINES_CONFIG=/app/engines.json \
  -e "$ENGINE_PASSWORD_ENV=$ADMIN_PASSWORD" \
  "$GATEWAY_IMAGE" >/dev/null
docker cp "$WORK_DIR/engines.json" "$GATEWAY_CONTAINER:/app/engines.json"
docker start "$GATEWAY_CONTAINER" >/dev/null

GATEWAY_URL="http://$(docker port "$GATEWAY_CONTAINER" 8080/tcp | head -n1)"
for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
  if curl -fsS "$GATEWAY_URL/healthz" >/dev/null 2>&1; then
    break
  fi
  if ((attempt == HEALTH_ATTEMPTS)); then
    echo "gateway never became healthy" >&2
    docker logs "$GATEWAY_CONTAINER" >&2
    exit 1
  fi
  sleep 1
done

count_surveys() {
  db_query "SELECT COUNT(*) FROM lime_surveys" | tr -d '[:space:]'
}
surveys_before="$(count_surveys)"

set +e
summary="$(
  PUBGW_SHARED_SECRET="$SHARED_SECRET" ENGINE_PASSWORD="$ADMIN_PASSWORD" \
    python3 - "$GATEWAY_URL" "$INSTANCE_ID" "$DEFINITION" "$GATEWAY_DIR" <<'PY'
"""宿主机上的驱动：按契约签名、调用网关，逐条断言 HTTP 层的结论。"""

import json
import os
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

url, instance, definition_path, gateway_dir = sys.argv[1:5]
sys.path.insert(0, gateway_dir)
from pubgw.auth import sign  # noqa: E402

SECRET = os.environ["PUBGW_SHARED_SECRET"].encode("utf-8")
ENGINE_PASSWORD = os.environ["ENGINE_PASSWORD"]
PUBLISH_TIMEOUT_SECONDS = 300
failures = []


def post(payload, signed=True):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signed:
        stamp = str(int(time.time()))
        headers.update({"X-Pubgw-Timestamp": stamp, "X-Pubgw-Signature": sign(SECRET, stamp, body)})
    request = Request(url + "/v1/publish", data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=PUBLISH_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def check(label, condition, detail=""):
    print("  [{}] {}{}".format("ok" if condition else "FAIL", label, ": " + detail if detail and not condition else ""),
          file=sys.stderr)
    if not condition:
        failures.append(label)


with open(definition_path, encoding="utf-8") as handle:
    definition = json.load(handle)
envelope = {"requestId": str(uuid.uuid4()), "engineInstanceId": instance, "definition": definition}
bodies = []

with urlopen(url + "/healthz", timeout=10) as response:
    check("healthz 200 {status: ok}", response.status == 200 and json.load(response) == {"status": "ok"})

status, raw = post(envelope, signed=False)
bodies.append(raw)
check("unsigned request -> 401", status == 401, "{} {!r}".format(status, raw[:200]))

status, raw = post(dict(envelope, requestId=str(uuid.uuid4()), engineInstanceId="nowhere-01"))
bodies.append(raw)
check("unknown instance -> 404", status == 404 and json.loads(raw) == {"error": "unknown_engine_instance"},
      "{} {!r}".format(status, raw[:200]))

started = time.monotonic()
status, first = post(envelope)
seconds = time.monotonic() - started
bodies.append(first)
published = json.loads(first)
result = published.get("result") or {}
binding = result.get("binding") or {}
check("publish -> 200 published", status == 200 and published.get("status") == "published",
      "{} {}".format(status, json.dumps(result.get("failures"), ensure_ascii=False)))
check("result.ok with a survey id", result.get("ok") is True and isinstance(result.get("surveyId"), int))
check("binding names the instance", binding.get("engineInstance") == instance)
check("binding keeps the definition uuid", binding.get("definitionUuid") == definition["uuid"])
check("fingerprint recorded", str(binding.get("fingerprint", "")).startswith("fm1:"))

status, replay = post(envelope)
bodies.append(replay)
check("replay by requestId -> same status and body", status == 200 and replay == first)

check("no response contains the engine password",
      all(ENGINE_PASSWORD.encode("utf-8") not in body for body in bodies))

print(json.dumps({"surveyId": result.get("surveyId"), "seconds": round(seconds, 1), "failures": failures}))
sys.exit(1 if failures else 0)
PY
)"
driver_status=$?
set -e

echo "$summary"
survey_id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["surveyId"] or "")' "$summary" 2>/dev/null || true)"
surveys_after="$(count_surveys)"

db_failures=0
if [[ -z "$survey_id" ]]; then
  echo "  [FAIL] no survey id to check in the database" >&2
  db_failures=1
else
  active="$(db_query "SELECT active FROM lime_surveys WHERE sid = $survey_id" | tr -d '[:space:]')"
  if [[ "$active" == "Y" ]]; then
    echo "  [ok] survey $survey_id is active in the database" >&2
  else
    echo "  [FAIL] survey $survey_id active='$active', expected Y" >&2
    db_failures=1
  fi
  if ((surveys_after == surveys_before + 1)); then
    echo "  [ok] exactly one survey created (replay did not publish again)" >&2
  else
    echo "  [FAIL] surveys went from $surveys_before to $surveys_after, expected +1" >&2
    db_failures=1
  fi
fi

if ((driver_status != 0 || db_failures != 0)); then
  echo "--- gateway log ---" >&2
  docker logs "$GATEWAY_CONTAINER" >&2
  echo "publish gateway service e2e FAILED ($TEST_DB)" >&2
  exit 1
fi
echo "publish gateway service e2e passed ($TEST_DB)"
