"""Owned doctor must use the exact full Docker IDs it admitted."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import importlib
import hashlib
import json
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class DoctorFullIdTest(unittest.TestCase):
    def setUp(self):
        self.doctor = importlib.import_module('doctor')
        self.owned = importlib.import_module('owned_maintenance')
        self.full_id = 'a' * 64
        self.compose = ['docker', 'compose', '-p', 'survey-production']

    def test_exposure_inspects_full_id_not_ambiguous_prefix(self):
        doctor = object.__new__(self.doctor.Doctor)
        doctor.listener_probe = SimpleNamespace(audit=lambda output, edge: [])
        observed = []
        def command(args):
            observed.append(args)
            if args[:len(self.compose)] == self.compose:
                value = self.full_id if '--no-trunc' in args else self.full_id[:12]
                return SimpleNamespace(stdout=json.dumps([{'ID':value}]))
            if args[:2] == ['docker','inspect']:
                self.assertEqual([self.full_id],args[2:])
                return SimpleNamespace(stdout='[]')
            return SimpleNamespace(stdout='')
        doctor._command = command
        doctor._runtime_exposure_check(self.compose)
        self.assertIn('--no-trunc', observed[0])

    def test_owned_runner_accepts_only_exact_expanded_ps(self):
        manager = SimpleNamespace(_compose=lambda version:self.compose,
            _run=lambda args,*a,**kw:subprocess.CompletedProcess(args,0,'[]',''))
        operation = SimpleNamespace(manager=manager,runtime=SimpleNamespace(data={'version':'0.3.25-rc.1'}),
            _identity=lambda:None,services={'platform':self.full_id})
        runner = self.owned.OwnedDoctorRunner(operation)
        result = runner.run(self.compose+['ps','--all','--no-trunc','--format','json'])
        self.assertEqual('[]',result.stdout)
        with self.assertRaises(self.doctor.DoctorRuntimeError):
            runner.run(['docker','inspect',self.full_id[:12]])
        with self.assertRaises(self.doctor.DoctorRuntimeError):
            runner.run(self.compose+['ps','--all','--no-trunc','--format','json','--filter','status=running'])

    def test_exposure_through_owned_runner_checks_all_nine_exact_ids(self):
        names = ['edge','admin-web','platform','publish-gateway','engine','engine-relay','engine-init','platform-db','engine-db']
        networks = {'edge':['edge','preview'],'admin-web':['edge'],'platform':['edge','internal'],
                    'publish-gateway':['preview','internal'],'engine':['edge','internal'],
                    'engine-relay':['internal'],'engine-init':['internal'],'platform-db':['internal'],'engine-db':['internal']}
        ids = {name: hashlib.sha256(name.encode('ascii')).hexdigest() for name in names}
        self.assertEqual(len(names),len({value[:12] for value in ids.values()}))
        query = self.compose+['ps','--all','--no-trunc','--format','json']
        rows = [{'ID':ids[name], 'Service':name} for name in names]
        def run(args,*a,**kw):
            if args == query:
                return subprocess.CompletedProcess(args,0,'\n'.join(json.dumps(row) for row in rows),'')
            if args == ['ss','-H','-ltnp']:
                return subprocess.CompletedProcess(args,0,'','')
            raise AssertionError('unregistered command')
        def inspect(method,path):
            cid=path.split('/')[2]
            name=next(name for name in names if ids[name]==cid)
            bindings={'80/tcp':[{'HostIp':'0.0.0.0','HostPort':'80'}],
                      '443/tcp':[{'HostIp':'0.0.0.0','HostPort':'443'}]} if name=='edge' else None
            return {'HostConfig':{'PortBindings':bindings},'NetworkSettings':{'Networks':
                    {'survey-production_'+network:{} for network in networks[name]}}}
        manager=SimpleNamespace(_compose=lambda version:self.compose,_run=run)
        operation=SimpleNamespace(manager=manager,runtime=SimpleNamespace(data={'version':'0.3.25-rc.1'}),
                   _identity=lambda:None,services=ids,api=SimpleNamespace(_json=inspect),
                   container_check=lambda cid,require_running=False:cid in ids.values())
        runner=self.owned.OwnedDoctorRunner(operation)
        doctor=object.__new__(self.doctor.Doctor)
        doctor.runner=runner
        doctor.listener_probe=SimpleNamespace(audit=lambda output,edge:[])
        self.assertEqual('ok',doctor._runtime_exposure_check(self.compose)['status'])
        for invalid in ([*ids.values()][:-1], [*ids.values(), 'f'*64],
                        [*ids.values()][:-1]+[ids[names[0]]],
                        [value[:12] for value in ids.values()]):
            with self.subTest(ids=invalid), self.assertRaises(self.doctor.DoctorRuntimeError):
                runner.run(['docker','inspect',*invalid])
        self.assertEqual(0, runner.run(['docker','inspect',*reversed(list(ids.values()))]).returncode)

if __name__=='__main__': unittest.main()
