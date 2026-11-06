#!/usr/bin/env python3
"""CI-only ownership handoff to a contained unprivileged Node reader."""
import os
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixture_browser_file import BrowserFixtureFile
from fixture_cgroup import CgroupScope
from fixture_handoff import make_handoff

if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
    os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or
    os.environ.get('GITHUB_REPOSITORY')!='lsgoodlionel/star-survey'):
    raise RuntimeError('dedicated hosted runner required')
node=shutil.which('node')
if not node: raise RuntimeError('Node is unavailable')
root=Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
helper=root/'platform/apps/admin-web/e2e/native-candidate/handoff.mjs'
if helper.is_symlink() or not helper.is_file(): raise RuntimeError('browser reader unavailable')
tenant,principal,connection=(str(uuid.uuid4()) for _ in range(3))
handoff=make_handoff(tenant,principal,connection)
fixture=BrowserFixtureFile.create(Path('/tmp').resolve(),handoff,65534,65534)
scope=None; process=None
try:
    code='''import {readHandoffFixture} from process.argv[1];
const item=await readHandoffFixture(process.argv[2],process.argv[3]);
if(item.tenantId!==process.argv[4]||item.principalId!==process.argv[5]) process.exit(1);
'''
    # Dynamic import supports an absolute path while never printing the values.
    code="const {readHandoffFixture}=await import(process.argv[1]);" + code.split(';',1)[1]
    scope=CgroupScope(65534,65534)
    process=scope.spawn([node,'--input-type=module','-e',code,helper.as_uri(),str(fixture.directory/'handoff.json'),
                         str(fixture.directory),tenant,principal],env={'PATH':'/usr/bin:/bin'})
    if process.wait(timeout=20)!=0: raise RuntimeError('unprivileged fixture reader failed')
    if not scope.empty():
        scope.kill_and_wait()
        raise RuntimeError('fixture reader left descendants')
    scope.close(); scope=None
    fixture.cleanup(); fixture=None
    print('unprivileged contained Node reader accepted the one-use private fixture; bound cleanup completed')
finally:
    if scope is not None:
        if not scope.empty(): scope.kill_and_wait()
        if process is not None: process.wait(timeout=5)
        if scope.empty(): scope.close()
    if fixture is not None: fixture.cleanup()
