import ast
from pathlib import Path
import unittest

source=Path(__file__).resolve().parents[1]/'scratch_candidate_stop.py'
tree=ast.parse(source.read_text())
outer=next(n for n in reversed(tree.body) if isinstance(n,ast.Try))
function=ast.FunctionDef(name='finish',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),
                         body=outer.finalbody,decorator_list=[])
compiled=compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(source),'exec')

class CloseAllTest(unittest.TestCase):
    def test_one_failed_close_does_not_skip_other_sessions(self):
        calls=[]
        class Session:
            def __init__(self,name,fail=False): self.name=name; self.fail=fail
            def close(self):
                calls.append(self.name)
                if self.fail: raise RuntimeError('sensitive')
        class Signal:
            SIGTERM=15; SIGINT=2
            def signal(self,*_): pass
        env={'signal':Signal(),'old_term':None,'old_int':None,'target':Session('target',True),
             'admin':Session('admin'),'emergency':Session('emergency'),'emergency_target':Session('emergency_target')}
        exec(compiled,env)
        with self.assertRaisesRegex(RuntimeError,'candidate session closure unverified'): env['finish']()
        self.assertEqual(calls,['target','admin','emergency','emergency_target'])

if __name__=='__main__': unittest.main()
