#!/usr/bin/env bash
# HTTP-only, best-effort checks for optional local AI endpoints.
set -uo pipefail

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OPENCLAW_BASE_URL="${OPENCLAW_BASE_URL:-}"
AI_SMOKE_MODEL="${AI_SMOKE_MODEL:-}"
AI_HEALTH_TIMEOUT="${AI_HEALTH_TIMEOUT:-3}"
CURL_BIN="${CURL_BIN:-curl}"

request() {
  local method="$1" url="$2" data="${3:-}" response status
  if [[ "$method" == POST ]]; then
    response=$("$CURL_BIN" --silent --show-error --max-time "$AI_HEALTH_TIMEOUT" \
      -X POST -H 'Content-Type: application/json' --data "$data" \
      -w $'\n%{http_code}' "$url" 2>/dev/null) || return 1
  else
    response=$("$CURL_BIN" --silent --show-error --max-time "$AI_HEALTH_TIMEOUT" \
      -w $'\n%{http_code}' "$url" 2>/dev/null) || return 1
  fi
  status="${response##*$'\n'}"
  response="${response%$'\n'*}"
  [[ "$status" =~ ^2[0-9][0-9]$ ]] || return 1
  printf '%s' "$response"
}

echo "Ollama HTTP health:"
tags=""
if tags=$(request GET "${OLLAMA_BASE_URL%/}/api/tags"); then
  echo "  healthy"
else
  echo "  unavailable (optional)"
fi

if [[ -n "$OPENCLAW_BASE_URL" ]]; then
  echo "OpenClaw HTTP health:"
  if request GET "${OPENCLAW_BASE_URL%/}/health" >/dev/null; then
    echo "  healthy"
  else
    echo "  unavailable (optional)"
  fi
else
  echo "OpenClaw HTTP health: skipped (OPENCLAW_BASE_URL not set)"
fi

model="$AI_SMOKE_MODEL"
if [[ -z "$model" && -n "$tags" ]]; then
  model=$(printf '%s' "$tags" | jq -r '.models[0].name // empty' 2>/dev/null || true)
fi

if [[ -z "$model" ]]; then
  echo "Ollama smoke: skipped (no AI_SMOKE_MODEL and no discoverable model)"
  exit 0
fi

payload=$(jq -cn --arg model "$model" \
  '{model:$model,prompt:"Reply with OK.",stream:false,options:{num_predict:4}}')
echo "Ollama smoke:"
if request POST "${OLLAMA_BASE_URL%/}/api/generate" "$payload" >/dev/null; then
  echo "  passed"
else
  echo "  failed (optional; development stack unaffected)"
fi
exit 0
