"""Isolated failure injection for the candidate stop diagnostic; no Docker access."""
import ast
import json
from pathlib import Path
import unittest

source=Path(__file__).resolve().parents[1]/'scratch_candidate_stop.py'
tree=ast.parse(source.read_text())
node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='action')
compiled=compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec')

class StopActionTest(unittest.TestCase):
    def run_scenario(self, failure):
        state={'gate':False,'running':False,'restart':'no','started':'old','failure':failure,'emergency':False}
        identities=[{'pid':i,'started':'s','database_oid':i,'database':'postgres'} for i in (1,2,3)]
        class Session:
            def __init__(self,index): self.identity=identities[index]
            def execute(self,sql):
                if sql.startswith('ALTER DATABASE'):
                    if 'true' in sql:
                        state['gate']=True
                        if state['failure']=='open': raise RuntimeError('ambiguous open')
                    else:
                        state['gate']=False; state['emergency']=True
                    return ''
                if 'json_agg' in sql: return json.dumps(identities)
                if 'pg_prepared_xacts' in sql: return '0'
                if 'datname=\'platform\' AND' in sql: return '0'
                if 'NOT datallowconn' in sql: return 't' if not state['gate'] else 'f'
                raise AssertionError('unexpected statement')
        admin,target,emergency=Session(0),Session(1),Session(2)
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
                if failure=='start': raise RuntimeError('ambiguous start')
        class Clock:
            value=0
            def monotonic(self): self.value+=200; return self.value
            def sleep(self,_): pass
        env={'check':check,'run':run,'pid':'a'*64,'expected':{'running':True,'started':'old','restart':'unless-stopped'},
             'admin':admin,'target':target,'emergency':emergency,'json':json,'inspect':lambda *_:item(),
             'check_platform_identity':item,'time':Clock(),'print':lambda *_:None}
        exec(compiled,env)
        with self.assertRaises(RuntimeError): env['action']()
        self.assertTrue(state['emergency'])
        self.assertFalse(state['gate'])
        self.assertFalse(state['running'])
        self.assertEqual(state['restart'],'no')

    def test_recloses_gate_after_uncertain_open_start_health_or_policy(self):
        for failure in ('open','start','health','policy'):
            with self.subTest(failure=failure): self.run_scenario(failure)

if __name__=='__main__': unittest.main()
