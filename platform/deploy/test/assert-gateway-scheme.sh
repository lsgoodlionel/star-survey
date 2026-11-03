#!/usr/bin/env bash
# Run inside an isolated real-stack harness after its gateway and engine start.
# Required: GATEWAY_CONTAINER, COMPOSE_PROJECT_NAME, SURVEY_TEST_PREFIX.
# Source this script after starting an isolated stack with production scheme
# configuration, gateway alias publish-gateway and engine alias test-web.
# It is not currently invoked by GitHub Actions; test_compose.py separately
# covers the scheme predicate on the existing quality gate.
set -euo pipefail
docker exec -i "$GATEWAY_CONTAINER" python - <<'PY'
import urllib.request,urllib.error,json
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k): return None
op=urllib.request.build_opener(NoRedirect())
def call(path,data=None):
 req=urllib.request.Request('http://test-web'+path,data=data,headers={'X-Forwarded-Proto':'https','Content-Type':'application/json'})
 try:
  with op.open(req,timeout=10) as r:return r.status,r.read()
 except urllib.error.HTTPError as e:return e.code,e.read()
for label,path in [('wrong route','/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=examStatus'),('duplicate','/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=examStatus&function=policyStatus'),('route override','/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=policyStatus&r=admin/index')]:
 status,_=call(path);assert status in (301,302,303,307,308),(label,status);print(label,'redirected')
status,_=call('/index.php/plugins/direct?plugin=MjyQuestionExtensions&function=extensionAnswers&sig=bad');assert status==401,('invalid signature',status);print('invalid signature rejected')
status,body=call('/index.php/admin/remotecontrol',json.dumps({'method':'get_session_key','params':['wrong','wrong'],'id':1}).encode())
response=json.loads(body)
result=response.get('result')
assert status==200 and response.get('id')==1 and response.get('error') is None
assert isinstance(result,dict) and result.get('error_code')=='ERR_INVALID_CREDENTIALS'
assert isinstance(result.get('status'),str) and result['status']
print('bad RPC credentials rejected with ERR_INVALID_CREDENTIALS')
PY
docker run --rm -i --network "${COMPOSE_PROJECT_NAME}_default" --entrypoint python "survey-publish-gateway:${SURVEY_TEST_PREFIX}" - <<'PY'
import urllib.request,urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k): return None
op=urllib.request.build_opener(NoRedirect())
for header in ('X-Forwarded-Proto','Forwarded-Proto'):
 req=urllib.request.Request('http://test-web/index.php/admin/remotecontrol',data=b'{}',headers={header:'https','Content-Type':'application/json'})
 try:
  with op.open(req,timeout=10) as r:status=r.status
 except urllib.error.HTTPError as e:status=e.code
 assert status in (301,302,303,307,308),(header,status)
 print('untrusted forged',header,'redirected')
PY
