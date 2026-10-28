#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECKER="$SCRIPT_DIR/ai-health-check.sh"
tmp="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp"
}
trap cleanup EXIT

cat > "$tmp/fake-curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
url="${@: -1}"
if [[ "$url" == http://unreachable/* ]]; then
  printf '%s\n503' '{}'
elif [[ "$url" == */api/tags ]]; then
  printf '%s\n200' '{"models": []}'
elif [[ "$url" == */health ]]; then
  printf '%s\n200' '{"status":"ok"}'
elif [[ "$url" == */api/generate ]]; then
  if [[ "${FAIL_SMOKE:-}" == 1 ]]; then
    printf '%s\n500' '{}'
  else
    printf '%s\n200' '{"response":"OK"}'
  fi
else
  printf '%s\n404' '{}'
fi
SH
chmod +x "$tmp/fake-curl"

output=$(CURL_BIN="$tmp/fake-curl" OLLAMA_BASE_URL="http://fake" OPENCLAW_BASE_URL= \
  AI_SMOKE_MODEL= "$CHECKER")
[[ "$output" == *"Ollama HTTP health:"*"healthy"*"OpenCLAW HTTP health: skipped"*"Ollama smoke: skipped"* ]] || \
  [[ "$output" == *"Ollama HTTP health:"*"healthy"*"OpenClaw HTTP health: skipped"*"Ollama smoke: skipped"* ]]

output=$(CURL_BIN="$tmp/fake-curl" OLLAMA_BASE_URL="http://fake" \
  OPENCLAW_BASE_URL="http://fake" AI_SMOKE_MODEL=tiny-model \
  "$CHECKER")
[[ "$output" == *"OpenClaw HTTP health:"*"healthy"*"Ollama smoke:"*"passed"* ]]

output=$(CURL_BIN="$tmp/fake-curl" FAIL_SMOKE=1 OLLAMA_BASE_URL="http://fake" \
  AI_SMOKE_MODEL=tiny-model "$CHECKER")
[[ "$output" == *"failed (optional; development stack unaffected)"* ]]

output=$(CURL_BIN="$tmp/fake-curl" OLLAMA_BASE_URL=http://unreachable AI_SMOKE_MODEL= \
  OPENCLAW_BASE_URL= "$CHECKER")
[[ "$output" == *"unavailable (optional)"*"Ollama smoke: skipped"* ]]

echo "AI health-check script tests passed"
