#!/usr/bin/env python3
"""Disposable hosted candidate stop/drain/restore diagnostic; no browser claim."""
import json, os, re, runpy, signal, subprocess, sys, time
from pathlib import Path
from fixture_checkpoint import bound_checkpoint_identity, run_checkpointed
from fixture_database import inspect_fixture_database, revalidate_fixture_database
from fixture_pinned_database import PinnedDatabaseSession
from native_acceptance_evidence import verify_fixture_owner

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
def validate_initial_platform(item, expected_release, expected_image):
    pid_value=item.get('Id',''); cfg=item.get('Config') or {}; labels_value=cfg.get('Labels') or {}
    state=item.get('State') or {}; host=item.get('HostConfig') or {}
    if (re.fullmatch(r'[0-9a-f]{64}',pid_value) is None or cfg.get('Image')!=expected_image or
        labels_value.get('com.docker.compose.project')!='survey-production' or
        labels_value.get('com.docker.compose.service')!='platform' or
        labels_value.get('com.docker.compose.project.working_dir')!=str(expected_release) or
        labels_value.get('com.docker.compose.project.config_files')!=str(expected_release/'compose.yml') or
        state.get('Running') is not True or not isinstance(state.get('StartedAt'),str) or
        host.get('RestartPolicy',{}).get('Name')!='unless-stopped'):
        raise RuntimeError('platform initial identity invalid')
    return pid_value
platform=inspect('container','survey-production-platform-1')
manifest_data=json.loads((release/'release.json').read_text())
pid=validate_initial_platform(platform,release,manifest_data['images']['PLATFORM_IMAGE'])
dbstart=inspect('container',dbid)['State']['StartedAt']
expected={'running':True,'started':platform['State']['StartedAt'],'restart':'unless-stopped'}
config=platform['Config']; labels=config['Labels']; image=config['Image']
networks={key:value['NetworkID'] for key,value in platform['NetworkSettings']['Networks'].items()}
if set(networks)!={'survey-production_edge','survey-production_internal'}: raise RuntimeError('network identity invalid')
def check_common():
    if run(['info','--format','{{.ID}}'])!=daemon: raise RuntimeError('daemon identity changed')
    verify_fixture_owner(work,work/'primary',marker)
    for name,nid in networks.items():
        network=inspect('network',nid); network_labels=network.get('Labels') or {}
        suffix=name.removeprefix('survey-production_')
        if (network.get('Id')!=nid or network_labels.get('com.docker.compose.project')!='survey-production' or
            network_labels.get('com.docker.compose.network')!=suffix or network.get('Internal') is not (suffix=='internal')):
            raise RuntimeError('candidate network changed')
    return True

def check_database():
    check_common()
    if revalidate_fixture_database(work,marker,release,dbid)!=dbid: raise RuntimeError('database binding changed')
    db=inspect('container',dbid)
    if db['State']['StartedAt']!=dbstart: raise RuntimeError('database restarted')
    dbnet=db.get('NetworkSettings',{}).get('Networks') or {}
    if (set(dbnet)!={'survey-production_internal'} or
        dbnet['survey-production_internal'].get('NetworkID')!=networks['survey-production_internal'] or
        (db.get('HostConfig') or {}).get('PortBindings') not in (None, {})):
        raise RuntimeError('database network binding changed')
    return True

def check_platform_identity():
    check_common()
    item=inspect('container',pid); cfg=item['Config']
    actual={key:value['NetworkID'] for key,value in item['NetworkSettings']['Networks'].items()}
    if item['Id']!=pid or cfg['Image']!=image or cfg['Labels']!=labels or actual!=networks:
        raise RuntimeError('platform identity changed')
    return item
def check():
    check_database()
    item=check_platform_identity(); state=item['State']; cfg=item['Config']; host=item['HostConfig']
    actual={key:value['NetworkID'] for key,value in item['NetworkSettings']['Networks'].items()}
    if (item['Id']!=pid or cfg['Image']!=image or cfg['Labels']!=labels or actual!=networks or
        state['Running'] is not expected['running'] or state['StartedAt']!=expected['started'] or
        host['RestartPolicy']['Name']!=expected['restart']): raise RuntimeError('platform binding changed')
    return True

def callback(expected_id):
    return expected_id==dbid and check_database()

identity=os.environ.get('NATIVE_AUTHOR_CHECKPOINT_IDENTITY','')
run_id=os.environ.get('GITHUB_RUN_ID',''); attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
manifest=os.environ.get('CANDIDATE_MANIFEST_SHA256','')
if identity!=bound_checkpoint_identity(work,run_id,attempt,manifest): raise RuntimeError('checkpoint identity differs')
os.mkdir(evidence,0o700)
admin=target=emergency=emergency_target=None
interrupted=False
mutation_started=False
old_term,old_int=signal.getsignal(signal.SIGTERM),signal.getsignal(signal.SIGINT)
def interrupt(_signum,_frame):
    global interrupted
    interrupted=True
def check_cancel():
    if interrupted: raise RuntimeError('candidate diagnostic interrupted')
def isolate_after_failure():
    # Independent attempts: a poisoned session cannot prevent later operations.
    failed=[]
    for name, session, statement in (
        ('platform_gate',emergency,'ALTER DATABASE platform WITH ALLOW_CONNECTIONS false;'),
        ('postgres_gate',emergency_target,'ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false;'),
        ('template_gate',emergency_target,'ALTER DATABASE template1 WITH ALLOW_CONNECTIONS false;'),
    ):
        try:
            if session is None: raise RuntimeError('emergency session missing')
            session.execute(statement)
        except BaseException:
            failed.append(name)
    try:
        item=check_platform_identity()
        if item['HostConfig']['RestartPolicy']['Name']!='no': run(['update','--restart=no',pid])
    except BaseException:
        failed.append('restart_policy')
    try:
        item=check_platform_identity()
        if item['State']['Running'] is True: run(['stop','--time','30',pid],timeout=45)
        item=check_platform_identity()
        if item['State']['Running'] is not False or item['HostConfig']['RestartPolicy']['Name']!='no':
            raise RuntimeError('platform stop unverified')
    except BaseException:
        failed.append('platform_stop')
    try:
        if emergency is None or emergency.execute('SELECT count(*) FROM pg_database WHERE datallowconn;')!='0':
            raise RuntimeError('database closure unverified')
    except BaseException:
        failed.append('gate_check')
    if failed: print('diagnostic: emergency isolation unverified; checkpoint remains pending')
    else: print('diagnostic: emergency gates closed and platform stopped; recovery remains unverified')
try:
    admin=PinnedDatabaseSession(dbid,'postgres',callback)
    target=PinnedDatabaseSession(dbid,'platform',callback)
    emergency=PinnedDatabaseSession(dbid,'postgres',callback)
    emergency_target=PinnedDatabaseSession(dbid,'platform',callback)
    signal.signal(signal.SIGTERM,interrupt); signal.signal(signal.SIGINT,interrupt)
    def action():
        global mutation_started
        check_cancel()
        if admin.execute("SELECT array_agg(datname::text ORDER BY datname::text)=ARRAY['platform','postgres','template1'] FROM pg_database WHERE datallowconn;")!='t':
            raise RuntimeError('unexpected connectable database')
        check(); check_cancel(); mutation_started=True
        run(['update','--restart=no',pid]); expected['restart']='no'; check(); check_cancel()
        admin.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS false;'); check_cancel()
        target.execute('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false;'); check_cancel()
        target.execute('ALTER DATABASE template1 WITH ALLOW_CONNECTIONS false;'); check_cancel()
        check(); run(['stop','--time','30',pid],timeout=45); expected['running']=False; check(); check_cancel()
        raw=admin.execute("SELECT coalesce(json_agg(json_build_object('pid',pid,'started',backend_start::text,"
            "'database_oid',datid::bigint,'database',datname) ORDER BY pid),'[]'::json) "
            "FROM pg_stat_activity WHERE backend_type='client backend';")
        clients=json.loads(raw)
        def key(item): return (item['pid'],item['started'],item['database_oid'],item['database'])
        if not isinstance(clients,list) or sorted(map(key,clients))!=sorted(map(key,[admin.identity,target.identity,emergency.identity,emergency_target.identity])):
            raise RuntimeError('unknown database client present')
        if admin.execute("SELECT count(*) FROM pg_prepared_xacts;")!='0': raise RuntimeError('prepared transaction present')
        if admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname='platform' AND (backend_type IS DISTINCT FROM 'client backend' OR state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL);")!='0':
            raise RuntimeError('database not quiet')
        if admin.execute('SELECT count(*) FROM pg_database WHERE datallowconn;')!='0': raise RuntimeError('database gates not closed')
        check_cancel(); print('diagnostic: bound candidate platform stopped and database writers drained')
        target.execute('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS true;'); check_cancel()
        target.execute('ALTER DATABASE template1 WITH ALLOW_CONNECTIONS true;'); check_cancel()
        admin.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS true;'); check_cancel()
        check(); run(['start',pid],timeout=45); check_cancel()
        state=inspect('container',pid)['State']
        if state.get('Running') is not True or state.get('StartedAt')==expected['started']:
            raise RuntimeError('candidate platform restart unconfirmed')
        expected['running']=True; expected['started']=state['StartedAt']; check(); check_cancel()
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            check(); check_cancel(); state=inspect('container',pid)['State']
            if (state.get('Health') or {}).get('Status')=='healthy': break
            time.sleep(1)
        else: raise RuntimeError('candidate platform health unverified')
        check(); run(['update','--restart=unless-stopped',pid]); expected['restart']='unless-stopped'; check(); check_cancel()
    def verify():
        check_cancel(); check()
        healthy=(admin.execute("SELECT array_agg(datname::text ORDER BY datname::text)=ARRAY['platform','postgres','template1'] FROM pg_database WHERE datallowconn;")=='t' and
                (inspect('container',pid)['State'].get('Health') or {}).get('Status')=='healthy')
        check_cancel()
        return healthy
    try:
        run_checkpointed(evidence,work,identity,action,verify,run_id=run_id,attempt=attempt,manifest_sha=manifest,
                         continue_finalization=lambda:not interrupted)
    except BaseException:
        if mutation_started: isolate_after_failure()
        raise
    print('diagnostic: platform restart and health verified; author preview has not run')
finally:
    signal.signal(signal.SIGTERM,old_term); signal.signal(signal.SIGINT,old_int)
    close_failed=False
    for session in (target,admin,emergency,emergency_target):
        if session is not None:
            try: session.close()
            except BaseException: close_failed=True
    if close_failed: raise RuntimeError('candidate session closure unverified')
