import json,os,pathlib,sys,uuid
root=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(root/'platform/tests/e2e'))
from response_read import DEFINITION, Database, DockerCurlTransport, RemoteControlClient, Publisher, SurveyDefinition, DISPLAY_ONLY_TYPES, insert_response
engine=os.environ['ENGINE_CONTAINER']; dbkind=os.environ['TEST_DB']; dbcontainer=os.environ['ENGINE_DB_CONTAINER']
client=RemoteControlClient(DockerCurlTransport(engine));client.login('admin','password')
try:
 payload=json.loads(DEFINITION.read_text());payload['uuid']=str(uuid.uuid4());payload['title']='isolated reminder fixture';payload['participants']=[{'ref':'completed','firstname':'syntheticA'},{'ref':'pending','firstname':'syntheticB'}];payload['settings']=dict(payload.get('settings') or {},anonymized='N')
 outcome=Publisher(client,engine_instance='isolated-engine').publish(SurveyDefinition.from_dict(payload));assert outcome.ok
 invitations={x['ref']:x['token'] for x in outcome.to_dict()['invitations']};assert set(invitations)=={'completed','pending'}
 bound=[(field.fieldname,q.type) for q in outcome.binding.questions for field in q.fields];written=[name for name,kind in bound if kind not in DISPLAY_ONLY_TYPES]
 insert_response(Database(dbkind,dbcontainer),dbkind,outcome.survey_id,1,written,token=invitations['completed'])
 target=pathlib.Path(os.environ['EVIDENCE_FILE']);target.write_text(json.dumps({'sid':outcome.survey_id,'completedToken':invitations['completed'],'pendingToken':invitations['pending']}));target.chmod(0o600)
finally:client.logout()
