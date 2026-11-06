#!/usr/bin/env python3
"""Signal actual supervisor process while stop/verify/finalize use simulated IO."""
import ast
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'platform/deploy/production/release'))
from fixture_checkpoint import bound_checkpoint_identity, run_checkpointed


def child(phase,work,evidence,ready,isolated):
    tree=ast.parse((ROOT/'scratch_candidate_stop.py').read_text())
    nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in ('action','verify','isolate_after_failure','check_cancel','interrupt')]
    state={'gates':{'platform':True,'postgres':True,'template1':True},'running':True,'restart':'unless-stopped','started':'old','action_done':False}
    identities=[{'pid':i,'started':'s','database_oid':i,'database':'postgres'} for i in (1,2,3,4)]
    def pause(): ready.touch(); time.sleep(1)
    class Session:
        def __init__(self,index): self.identity=identities[index]; self.poisoned=False
        def execute(self,sql):
            if sql.startswith('ALTER DATABASE'):
                state['gates'][sql.split()[2]]='true' in sql; return ''
            if 'array_agg' in sql: return 't' if all(state['gates'].values()) else 'f'
            if 'json_agg' in sql: return json.dumps(identities)
            if 'count(*) FROM pg_database' in sql: return str(sum(state['gates'].values()))
            if 'pg_prepared_xacts' in sql or "datname='platform' AND" in sql: return '0'
            raise RuntimeError('unexpected SQL')
    def check():
        if phase=='verify' and state['action_done'] and not ready.exists(): pause()
        return True
    def item():
        return {'State':{'Running':state['running'],'StartedAt':state['started'],'Health':{'Status':'healthy'}},
                'HostConfig':{'RestartPolicy':{'Name':state['restart']}}}
    def run(args,timeout=30):
        if args[0]=='update': state['restart']='no' if args[1]=='--restart=no' else 'unless-stopped'
        elif args[0]=='stop':
            if phase=='stop' and not ready.exists(): pause()
            state['running']=False
        elif args[0]=='start': state['running']=True; state['started']='new'
    def output(message):
        if message.startswith('diagnostic: emergency'): isolated.write_text(message)
    env={'interrupted':False,'mutation_started':False,'check':check,'run':run,'pid':'a'*64,
         'expected':{'running':True,'started':'old','restart':'unless-stopped'},'admin':Session(0),'target':Session(1),
         'emergency':Session(2),'emergency_target':Session(3),'json':json,'inspect':lambda *_:item(),
         'check_platform_identity':item,'time':time,'print':output}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROOT/'scratch_candidate_stop.py'),'exec'),env)
    signal.signal(signal.SIGTERM,env['interrupt'])
    identity=bound_checkpoint_identity(work,'12','1','b'*64)
    original=os.fsync; count=[0]
    def fsync(fd):
        count[0]+=1
        if phase=='receipt' and count[0]==3: pause()
        return original(fd)
    def action(): env['action'](); state['action_done']=True
    try:
        with mock.patch('fixture_checkpoint.os.fsync',side_effect=fsync):
            run_checkpointed(evidence,work,identity,action,env['verify'],run_id='12',attempt='1',manifest_sha='b'*64,
                             continue_finalization=lambda:not env['interrupted'])
    except BaseException:
        if env['mutation_started']: env['isolate_after_failure']()
        return 1
    return 0


def main():
    if len(sys.argv)==7 and sys.argv[1]=='child':
        return child(sys.argv[2],*(Path(value) for value in sys.argv[3:]))
    for phase in ('stop','verify','receipt'):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); work=root/'work'; evidence=root/'evidence'; ready=root/'ready'; isolated=root/'isolated'
            work.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
            process=subprocess.Popen([sys.executable,__file__,'child',phase,str(work),str(evidence),str(ready),str(isolated)],
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+10
                while not ready.exists() and process.poll() is None and time.monotonic()<deadline: time.sleep(.02)
                if not ready.exists(): raise RuntimeError('signal phase not reached: '+phase)
                process.send_signal(signal.SIGTERM)
                if process.wait(timeout=10)==0: raise RuntimeError('interruption accepted: '+phase)
                if not (evidence/'fixture-recovery.pending').exists() or (evidence/'fixture-recovery.verified').exists():
                    raise RuntimeError('checkpoint state unsafe: '+phase)
                if not isolated.exists(): raise RuntimeError('emergency isolation not attempted: '+phase)
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)
    print('real SIGTERM during stop, verify and receipt retained pending and attempted isolation')
    return 0

if __name__=='__main__': raise SystemExit(main())
