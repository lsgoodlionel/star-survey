#!/usr/bin/env python3
"""Disposable hosted-runner Compose topology probe, not candidate authorization."""
import json, os, re, secrets, subprocess, tempfile, time
from pathlib import Path
from exclusive_probe import require_container_absent
D=['docker','--host','unix:///var/run/docker.sock']

def run(args, check=True, timeout=40):
    p=subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if check and p.returncode: raise RuntimeError('isolated Compose command failed')
    return p

def inspect(kind, ident):
    items=json.loads(run(D+['inspect','--type',kind,ident]).stdout)
    if not isinstance(items,list) or len(items)!=1: raise RuntimeError('ambiguous resource')
    return items[0]

def main():
    if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
        os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or
        os.environ.get('GITHUB_REPOSITORY')!='lsgoodlionel/star-survey'):
        raise RuntimeError('dedicated hosted runner required')
    project='fixture'+secrets.token_hex(12)
    daemon=run(D+['info','--format','{{.ID}}']).stdout.strip()
    if not daemon: raise RuntimeError('daemon identity invalid')
    image=run(D+['image','inspect','postgres:16-alpine','--format','{{.Id}}']).stdout.strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image): raise RuntimeError('image identity invalid')
    doc={'name':project,'services':{
        'platform-db':{'image':image,'environment':{'POSTGRES_HOST_AUTH_METHOD':'trust'},'tmpfs':['/var/lib/postgresql/data'],
                       'networks':['internal'],'restart':'unless-stopped'},
        'platform':{'image':image,'entrypoint':['/bin/sh','-c'],
                    'command':['while :; do PGAPPNAME=fixture_platform psql -h platform-db -U postgres -d postgres -c "select pg_sleep(1)" >/dev/null 2>&1 || true; done'],
                    'networks':['edge','internal'],'restart':'unless-stopped'}},
         'networks':{'edge':{},'internal':{'internal':True}}}
    ids={}; nets={}; directory=tempfile.TemporaryDirectory(prefix='survey-compose-probe-')
    path=Path(directory.name)/'compose.json'; path.write_text(json.dumps(doc)); os.chmod(path,0o600)
    compose=D+['compose','--project-name',project,'--file',str(path)]
    try:
        # Random project must not already own containers or networks.
        if run(D+['ps','-aq','--filter','label=com.docker.compose.project='+project]).stdout.strip(): raise RuntimeError('project not empty')
        if run(D+['network','ls','-q','--filter','label=com.docker.compose.project='+project]).stdout.strip(): raise RuntimeError('network project not empty')
        run(compose+['up','-d'],timeout=100)
        for service in ('platform-db','platform'):
            cid=run(compose+['ps','-aq',service]).stdout.strip()
            if not re.fullmatch(r'[0-9a-f]{64}',cid): raise RuntimeError('container identity invalid')
            x=inspect('container',cid); labels=x['Config']['Labels']; state=x['State']
            if (x['Id']!=cid or x['Image']!=image or labels.get('com.docker.compose.project')!=project or
                labels.get('com.docker.compose.service')!=service or state['Running'] is not True or
                x['HostConfig']['RestartPolicy']['Name']!='unless-stopped'):
                raise RuntimeError('container binding invalid')
            ids[service]=(cid,state['StartedAt'])
        for key in ('edge','internal'):
            name=project+'_'+key; net=inspect('network',name); nid=net['Id']; labels=net['Labels']
            if (not re.fullmatch(r'[0-9a-f]{64}',nid) or labels.get('com.docker.compose.project')!=project or
                labels.get('com.docker.compose.network')!=key or net['Internal'] is not (key=='internal')):
                raise RuntimeError('network binding invalid')
            nets[key]=nid
        for service, expected in (('platform',{'edge','internal'}),('platform-db',{'internal'})):
            x=inspect('container',ids[service][0]); networks=x['NetworkSettings']['Networks']
            if set(networks)!={project+'_'+k for k in expected} or any(networks[project+'_'+k]['NetworkID']!=nets[k] for k in expected):
                raise RuntimeError('container network mismatch')
        db, platform=ids['platform-db'][0],ids['platform'][0]
        if inspect('container',db)['NetworkSettings'].get('Ports') not in ({'5432/tcp':None},{}):
            raise RuntimeError('database published port exists')
        deadline=time.monotonic()+60
        def sql(statement, database='postgres'):
            p=run(D+['exec',db,'psql','-X','-q','-A','-t','-U','postgres','-d',database,'-v','ON_ERROR_STOP=1','-c',statement],check=False)
            return p
        while time.monotonic()<deadline:
            q=sql("SELECT count(*) FROM pg_stat_activity WHERE application_name='fixture_platform';")
            if q.returncode==0 and q.stdout.strip()=='1': break
            time.sleep(.1)
        else: raise RuntimeError('platform writer not observed')
        def require_stoppable():
            x=inspect('container',platform)
            if x['Id']!=platform or x['State']['StartedAt']!=ids['platform'][1] or x['HostConfig']['RestartPolicy']['Name']!='no':
                raise RuntimeError('platform stop preconditions not established')
        try: require_stoppable()
        except RuntimeError: pass
        else: raise RuntimeError('automatic restart policy incorrectly accepted')
        # Isolated target only: disable restart before stopping its exact ID.
        run(D+['update','--restart=no',platform]); require_stoppable()
        # Disable new target sessions from a separate maintenance database.
        if sql('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false;','template1').returncode: raise RuntimeError('database gate failed')
        run(D+['stop','--time','3',platform],timeout=15)
        x=inspect('container',platform)
        if x['Id']!=platform or x['State']['Running'] is not False or x['State']['StartedAt']!=ids['platform'][1] or x['HostConfig']['RestartPolicy']['Name']!='no':
            raise RuntimeError('platform stop unconfirmed')
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            q=sql("SELECT count(*) FROM pg_stat_activity WHERE datname='postgres' AND backend_type='client backend';",'template1')
            if q.returncode==0 and q.stdout.strip()=='0': break
            time.sleep(.05)
        else: raise RuntimeError('platform database clients remain')
        if sql('SELECT 1;').returncode==0: raise RuntimeError('closed gate accepted connection')
        print('exact platform and network identities verified; restart disabled; platform stopped; database clients drained')
        print('scope: synthetic Compose services on trusted dedicated runner, not candidate browser acceptance')
    finally:
        # Only exact IDs whose labels are rechecked may be touched. A failure
        # leaves an explicitly failed hosted job; never infer cleanup success.
        for service,(cid,_) in ids.items():
            x=inspect('container',cid)
            if x['Id']!=cid or x['Config']['Labels'].get('com.docker.compose.project')!=project or x['Config']['Labels'].get('com.docker.compose.service')!=service:
                raise RuntimeError('cleanup container binding changed')
            run(D+['rm','-f',cid],timeout=20)
            before=run(D+['info','--format','{{.ID}}'],check=False)
            gone=run(D+['inspect','--type','container',cid],check=False)
            after=run(D+['info','--format','{{.ID}}'],check=False)
            require_container_absent(gone,cid,daemon,before,after)
        for key,nid in nets.items():
            n=inspect('network',nid)
            if n['Id']!=nid or n['Labels'].get('com.docker.compose.project')!=project or n['Labels'].get('com.docker.compose.network')!=key:
                raise RuntimeError('cleanup network binding changed')
            run(D+['network','rm',nid],timeout=20)
            before=run(D+['info','--format','{{.ID}}'],check=False)
            gone=run(D+['inspect','--type','network',nid],check=False)
            after=run(D+['info','--format','{{.ID}}'],check=False)
            errors={f'Error: No such object: {nid}',f'Error: No such network: {nid}',f'Error response from daemon: network {nid} not found'}
            if (before.returncode or after.returncode or before.stdout.strip()!=daemon or after.stdout.strip()!=daemon or
                gone.returncode==0 or gone.stderr.strip() not in errors): raise RuntimeError('network absence unconfirmed')
        directory.cleanup()

if __name__=='__main__': main()
