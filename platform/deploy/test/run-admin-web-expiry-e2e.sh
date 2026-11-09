#!/bin/bash
set -euo pipefail
# This isolated focused gate does not replace the standard full suite.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SURVEY_TEST_PREFIX="${SURVEY_TEST_PREFIX:-adminweb}-expiry"
export COMPOSE_PROJECT_NAME="$SURVEY_TEST_PREFIX"
export E2E_SUITE=narrow
unset ADMIN_WEB_E2E_KEEP
source "$SCRIPT_DIR/run-admin-web-e2e.sh"
COMPOSE+=( -f "$SCRIPT_DIR/admin-web-expiry.compose.yml" )
run_playwright() {
  echo 'Natural expiry scoped proof, not full gate' >&2
  (cd "$ADMIN_WEB_DIR" && npm run e2e -- --project=chromium-expiry --retries=0)
}
verify_and_archive_run_root() {
  echo 'Natural expiry scoped cross-layer proof using original validate_evidence, not full gate' >&2
  local success_marker="$ADMIN_WEB_TEST_RESULTS_DIR/playwright-suite-success.txt"
  local root_id_file="$ADMIN_WEB_TEST_RESULTS_DIR/run-root-resource-id.txt"
  [[ -f "$success_marker" && "$(<"$success_marker")" == "passed" ]] || fail "Playwright did not write a valid suite success marker"
  [[ -f "$root_id_file" ]] || fail "Playwright did not write run root evidence"
  local proof_status=0
  python3 - "$GATE" "$PLATFORM_URL" "$ADMIN_WEB_METADATA_FILE" "$ADMIN_WEB_RESULT_FILE" "$PLATFORM_DB_CONTAINER" "$TEST_PREFIX-$DB_SERVICE" "$TEST_DB" "$GATEWAY_CONTAINER" <<'PY' || proof_status=$?
import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('admin_web_gate',sys.argv[1]); g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
_,_,base,meta,result,platformdb,enginedb,kind,gateway=sys.argv
m=g.validate_metadata(g._load_object(Path(meta),'metadata')); r=g.validate_result(g._load_object(Path(result),'browser result'))
a=g.Api(base,g.mint_token(g.jwt_secret(),m['actorId'],m['tenantId'],[])); evidence={}
for key,path in [('survey','/v1/surveys/'+r['surveyId']),('versions','/v1/surveys/'+r['surveyId']+'/versions'),('route','/v1/survey-routes/'+r['surveyId'])]:
 status,payload=a.call('GET',path); g.expect(status==200,'scoped proof reads '+key); evidence[key]=payload
v=next(v for v in evidence['versions'] if v['version']==r['version'])
g.validate_evidence(m,r,evidence,g._platform_db_evidence(platformdb,'platform',m['tenantId'],r),g._engine_db_evidence(enginedb,kind,v['engineSid']),g._gateway_publish_calls(gateway),1)
PY
  python3 "$GATE" --base-url "$PLATFORM_URL" archive-run-root --jwt-file "$ADMIN_WEB_JWT_FILE" --root-id-file "$ADMIN_WEB_TEST_RESULTS_DIR/run-root-resource-id.txt" || return 1
  return "$proof_status"
}
main --fresh
