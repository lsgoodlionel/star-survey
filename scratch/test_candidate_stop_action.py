"""Isolated failure injection for the candidate stop diagnostic; no Docker access."""
import ast
import json
from pathlib import Path
import unittest

source=Path(__file__).resolve().parents[1]/'scratch_candidate_stop.py'
tree=ast.parse(source.read_text())
nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in ('action','verify','isolate_after_failure')]
compiled=compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec')

class StopActionTest(unittest.TestCase):
    def run_scenario(self, failure):
        state={'gates':{'platform':True,'postgres':True,'template1':True},'running':False,'restart':'no','started':'old','failure':failure,'emergency':False,'messages':[],'action_done':False}
        identities=[{'pid':i,'started':'s','database_oid':i,'database':'postgres'} for i in (1,2,3,4)]
        class Session:
            def __init__(self,index): self.identity=identities[index]; self.index=index
            def execute(self,sql):
                if sql.startswith('ALTER DATABASE'):
                    database=sql.split()[2]
                    if 'true' in sql:
                        state['gates'][database]=True
                        if state['failure']=='open': raise RuntimeError('ambiguous open')
                    else:
                        if self.index==2 and failure=='start_emergency': raise RuntimeError('emergency unavailable')
                        state['gates'][database]=False
                        if self.index==2: state['emergency']=True
                    return ''
                if 'json_agg' in sql:
                    extra = [{'pid':99,'started':'s','database_oid':99,'database':'postgres'}] if failure=='unknown' else []
                    return json.dumps(identities + extra)
                if 'array_agg' in sql: return 'f' if failure=='verify' and state['action_done'] else ('t' if all(state['gates'].values()) else 'f')
                if 'count(*) FROM pg_database' in sql: return str(sum(state['gates'].values()))
                if 'pg_prepared_xacts' in sql: return '0'
                if 'datname=\'platform\' AND' in sql: return '0'
                if 'NOT datallowconn' in sql: return 't' if not state['gates']['platform'] else 'f'
                raise AssertionError('unexpected statement')
        admin,target,emergency,emergency_target=Session(0),Session(1),Session(2),Session(3)
        def check(): return True
        def item():
            return {'State':{'Running':state['running'],'StartedAt':state['started'],
                             'Health':{'Status':'unhealthy' if failure=='health' else 'healthy'}},
                    'HostConfig':{'RestartPolicy':{'Name':state['restart']}}}
        def run(args,timeout=30):
            if args[0]=='update':
                state['restart']='no' if args[1]=='--restart=no' else 'unless-stopped'
                if failure=='policy' and args[1]=='--restart=unless-stopped': raise RuntimeError('ambiguous update')
            elif args[0]=='stop': state['running']=False
            elif args[0]=='start':
                state['running']=True; state['started']='new'
                if failure in ('start','start_emergency'): raise RuntimeError('ambiguous start')
        class Clock:
            value=0
            def monotonic(self): self.value+=200 if failure=='health' else 1; return self.value
            def sleep(self,_): pass
        env={'check':check,'run':run,'pid':'a'*64,'expected':{'running':True,'started':'old','restart':'unless-stopped'},
             'admin':admin,'target':target,'emergency':emergency,'emergency_target':emergency_target,'json':json,'check_cancel':lambda:None,'mutation_started':False,'inspect':lambda *_:item(),
             'check_platform_identity':item,'time':Clock(),'print':lambda message:state['messages'].append(message)}
        exec(compiled,env)
        with self.assertRaises(RuntimeError):
            try:
                env['action']()
                state['action_done']=True
                if env['verify']() is not True: raise RuntimeError('verification failed')
            except BaseException:
                if env['mutation_started']: env['isolate_after_failure']()
                raise
        if failure=='unknown':
            self.assertFalse(any(state['gates'].values()))
            self.assertFalse(state['running'])
            return
        if failure=='start_emergency':
            self.assertIn('diagnostic: emergency isolation unverified; checkpoint remains pending',state['messages'])
            self.assertFalse(state['running'])
            return
        self.assertTrue(state['emergency'])
        self.assertFalse(any(state['gates'].values()))
        self.assertFalse(state['running'])
        self.assertEqual(state['restart'],'no')

    def test_recloses_gate_after_uncertain_open_start_health_or_policy(self):
        for failure in ('open','start','health','policy','verify','unknown','start_emergency'):
            with self.subTest(failure=failure): self.run_scenario(failure)

if __name__=='__main__': unittest.main()
