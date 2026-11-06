import ast
from pathlib import Path
import unittest

source = Path(__file__).resolve().parents[1] / 'scratch_candidate_stop.py'
tree = ast.parse(source.read_text())
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ('check_database', 'check_platform_identity', 'check')]
compiled = compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec')

class CandidateStopGuardTest(unittest.TestCase):
    def setUp(self):
        self.dbid, self.pid = 'a'*64, 'b'*64
        networks = {'survey-production_edge':'c'*64, 'survey-production_internal':'d'*64}
        labels = {'com.docker.compose.project':'survey-production','com.docker.compose.service':'platform'}
        self.platform = {'Id':self.pid,'State':{'Running':True,'StartedAt':'first'},'Config':{'Image':'fixed','Labels':dict(labels)},
            'HostConfig':{'RestartPolicy':{'Name':'unless-stopped'}},'NetworkSettings':{'Networks':{k:{'NetworkID':v} for k,v in networks.items()}}}
        self.db = {'State':{'StartedAt':'db-start'},'HostConfig':{'PortBindings':{}},
                   'NetworkSettings':{'Networks':{'survey-production_internal':{'NetworkID':'d'*64}}}}
        self.networks = {v:{'Id':v,'Internal':k.endswith('_internal'),
            'Labels':{'com.docker.compose.project':'survey-production','com.docker.compose.network':k.removeprefix('survey-production_')}} for k,v in networks.items()}
        self.daemon='daemon'; self.returned_db=self.dbid
        self.env={'daemon':'daemon','run':lambda _:self.daemon,'revalidate_fixture_database':lambda *_:self.returned_db,
            'inspect':self.inspect,'work':None,'marker':None,'release':None,'dbid':self.dbid,'dbstart':'db-start',
            'networks':networks,'pid':self.pid,'labels':labels,'image':'fixed',
            'expected':{'running':True,'started':'first','restart':'unless-stopped'}}
        exec(compiled,self.env)
    def inspect(self,kind,ident):
        if kind=='network': return self.networks[ident]
        if ident==self.dbid: return self.db
        if ident==self.pid: return self.platform
        raise RuntimeError('unknown target')
    def test_exact_binding(self): self.assertTrue(self.env['check']())
    def test_rejects_replacement_restart_and_network_changes(self):
        for mutation in ('daemon','db_id','db_restart','db_network','db_port','network_replaced','network_external',
                         'platform_restart','platform_stopped','platform_policy','platform_replaced','platform_network','platform_labels'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation=='daemon': self.daemon='changed'
                elif mutation=='db_id': self.returned_db='f'*64
                elif mutation=='db_restart': self.db['State']['StartedAt']='second'
                elif mutation=='db_network': self.db['NetworkSettings']['Networks']['survey-production_internal']['NetworkID']='f'*64
                elif mutation=='db_port': self.db['HostConfig']['PortBindings']={'5432/tcp':[{}]}
                elif mutation=='network_replaced': self.networks['d'*64]['Id']='f'*64
                elif mutation=='network_external': self.networks['d'*64]['Internal']=False
                elif mutation=='platform_restart': self.platform['State']['StartedAt']='second'
                elif mutation=='platform_stopped': self.platform['State']['Running']=False
                elif mutation=='platform_policy': self.platform['HostConfig']['RestartPolicy']['Name']='always'
                elif mutation=='platform_replaced': self.platform['Id']='f'*64
                elif mutation=='platform_network': self.platform['NetworkSettings']['Networks']['survey-production_edge']['NetworkID']='f'*64
                elif mutation=='platform_labels': self.platform['Config']['Labels']['com.docker.compose.project']='other'
                with self.assertRaises(RuntimeError): self.env['check']()

if __name__=='__main__': unittest.main()
