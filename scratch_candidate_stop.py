#!/usr/bin/env python3
"""Disposable hosted candidate stop/drain/restore diagnostic; no browser claim."""
import json, os, re, runpy, subprocess, sys, time
from pathlib import Path
from fixture_checkpoint import bound_checkpoint_identity, run_checkpointed
from fixture_database import inspect_fixture_database, revalidate_fixture_database
from fixture_pinned_database import PinnedDatabaseSession

if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
    os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or
    os.environ.get('GITHUB_REPOSITORY')!='lsgoodlionel/star-survey' or len(sys.argv)!=3):
    raise RuntimeError('dedicated hosted candidate required')
work=Path(sys.argv[1]); evidence=Path(sys.argv[2]); marker=work/'owner.json'
# Reuse the separate read-only identity diagnostic before any mutation.
original_argv=sys.argv
try:
    sys.argv=[str(Path(__file__).with_name('scratch_candidate_binding.py')),str(work)]
    runpy.run_path(sys.argv[0],run_name='candidate_binding')
finally:
    sys.argv=original_argv
release,dbid=inspect_fixture_database(work,marker)
D=['docker','--host','unix:///var/run/docker.sock']
def run(args, timeout=30):
    p=subprocess.run(D+args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError('candidate diagnostic command failed')
    return p.stdout.strip()
def inspect(kind, ident):
    rows=json.loads(run(['inspect','--type',kind,ident]))
    if not isinstance(rows,list) or len(rows)!=1: raise RuntimeError('candidate inspection ambiguous')
    return rows[0]
daemon=run(['info','--format','{{.ID}}'])
platform=inspect('container','survey-production-platform-1'); pid=platform['Id']
if not re.fullmatch(r'[0-9a-f]{64}',pid): raise RuntimeError('platform ID invalid')
dbstart=inspect('container',dbid)['State']['StartedAt']
expected={'running':True,'started':platform['State']['StartedAt'],'restart':'unless-stopped'}
config=platform['Config']; labels=config['Labels']; image=config['Image']
networks={key:value['NetworkID'] for key,value in platform['NetworkSettings']['Networks'].items()}
if set(networks)!={'survey-production_edge','survey-production_internal'}: raise RuntimeError('network identity invalid')
def check():
    if run(['info','--format','{{.ID}}'])!=daemon: raise RuntimeError('daemon identity changed')
    if revalidate_fixture_database(work,marker,release,dbid)!=dbid: raise RuntimeError('database binding changed')
    db=inspect('container',dbid)
    if db['State']['StartedAt']!=dbstart: raise RuntimeError('database restarted')
    item=inspect('container',pid); state=item['State']; cfg=item['Config']; host=item['HostConfig']
    actual={key:value['NetworkID'] for key,value in item['NetworkSettings']['Networks'].items()}
    if (item['Id']!=pid or cfg['Image']!=image or cfg['Labels']!=labels or actual!=networks or
        state['Running'] is not expected['running'] or state['StartedAt']!=expected['started'] or
        host['RestartPolicy']['Name']!=expected['restart']): raise RuntimeError('platform binding changed')
    return True

def callback(expected_id):
    return expected_id==dbid and check()

identity=os.environ.get('NATIVE_AUTHOR_CHECKPOINT_IDENTITY','')
run_id=os.environ.get('GITHUB_RUN_ID',''); attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
manifest=os.environ.get('CANDIDATE_MANIFEST_SHA256','')
if identity!=bound_checkpoint_identity(work,run_id,attempt,manifest): raise RuntimeError('checkpoint identity differs')
os.mkdir(evidence,0o700)
admin=target=None
try:
    admin=PinnedDatabaseSession(dbid,'postgres',callback)
    target=PinnedDatabaseSession(dbid,'platform',callback)
    def action():
        check(); run(['update','--restart=no',pid]); expected['restart']='no'; check()
        admin.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS false;')
        check(); run(['stop','--time','30',pid],timeout=45); expected['running']=False; check()
        raw=admin.execute("SELECT coalesce(json_agg(json_build_object('pid',pid,'started',backend_start::text,"
            "'database_oid',datid::bigint,'database',datname) ORDER BY pid),'[]'::json) "
            "FROM pg_stat_activity WHERE backend_type='client backend';")
        clients=json.loads(raw)
        def key(item): return (item['pid'],item['started'],item['database_oid'],item['database'])
        if not isinstance(clients,list) or sorted(map(key,clients))!=sorted(map(key,[admin.identity,target.identity])):
            raise RuntimeError('unknown database client present')
        if admin.execute("SELECT count(*) FROM pg_prepared_xacts;")!='0': raise RuntimeError('prepared transaction present')
        if admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname='platform' AND (backend_type IS DISTINCT FROM 'client backend' OR state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL);")!='0':
            raise RuntimeError('database not quiet')
        if admin.execute("SELECT NOT datallowconn FROM pg_database WHERE datname='platform';")!='t': raise RuntimeError('gate not closed')
        print('diagnostic: bound candidate platform stopped and database writers drained')
        # Only the successful diagnostic path may reopen and restart.
        admin.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS true;')
        check(); run(['start',pid],timeout=45)
        state=inspect('container',pid)['State']
        if state.get('Running') is not True or state.get('StartedAt')==expected['started']:
            raise RuntimeError('candidate platform restart unconfirmed')
        expected['running']=True; expected['started']=state['StartedAt']; check()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            check(); state=inspect('container',pid)['State']
            if (state.get('Health') or {}).get('Status')=='healthy': break
            time.sleep(1)
        else: raise RuntimeError('candidate platform health unverified')
        check(); run(['update','--restart=unless-stopped',pid]); expected['restart']='unless-stopped'; check()
    def verify():
        check()
        return (admin.execute("SELECT datallowconn FROM pg_database WHERE datname='platform';")=='t' and
                (inspect('container',pid)['State'].get('Health') or {}).get('Status')=='healthy')
    run_checkpointed(evidence,work,identity,action,verify,run_id=run_id,attempt=attempt,manifest_sha=manifest)
    print('diagnostic: platform restart and health verified; author preview has not run')
finally:
    if target is not None: target.close()
    if admin is not None: admin.close()
