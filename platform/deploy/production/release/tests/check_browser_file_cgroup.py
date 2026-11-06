#!/usr/bin/env python3
"""CI-only ownership handoff to a contained unprivileged Node reader."""
import os
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixture_browser_file import BrowserFixtureError, BrowserFixtureFile, require_unused_worker_ids
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
module_directory=tempfile.TemporaryDirectory(prefix='survey-reader-',dir='/tmp')
module_root=Path(module_directory.name); os.chmod(module_root,0o755)
module_copy=module_root/'handoff.mjs'; shutil.copyfile(helper,module_copy); os.chmod(module_copy,0o444)
if hashlib.sha256(helper.read_bytes()).digest()!=hashlib.sha256(module_copy.read_bytes()).digest():
    raise RuntimeError('browser reader copy differs')
tenant,principal,connection=(str(uuid.uuid4()) for _ in range(3))
handoff=make_handoff(tenant,principal,connection)
# Existing processes with the selected numeric IDs must block issuance.
probe=CgroupScope(61001,61001)
process=probe.spawn(['/usr/bin/sleep','5'],env={'PATH':'/usr/bin:/bin'})
try:
    try: require_unused_worker_ids(61001,61001)
    except BrowserFixtureError: pass
    else: raise RuntimeError('live worker identity was accepted')
finally:
    probe.kill_and_wait(); process.wait(timeout=5); probe.close()
fixture=BrowserFixtureFile.create(Path('/tmp').resolve(),handoff,61001,61001)
scope=None; process=None
try:
    # Fixed exit codes reveal the failing stage without printing fixture data.
    code='''let reader; try { reader=await import(process.argv[1]); } catch { process.exit(21); }
let item; try { item=await reader.readTrustedHandoffFixture(process.argv[2],process.argv[3],process.argv[4],process.argv[5]); } catch { process.exit(22); }
try { await reader.readTrustedHandoffFixture(process.argv[2],process.argv[3],process.argv[4],'00000000-0000-4000-8000-000000000000'); process.exit(24); } catch {}
const fs=await import('node:fs/promises');
try { await fs.appendFile(process.argv[2],'x'); process.exit(25); } catch (e) { if(e.code!=='EACCES') process.exit(26); }
try { await fs.unlink(process.argv[2]); process.exit(27); } catch (e) { if(e.code!=='EACCES') process.exit(28); }
if(item.tenantId!==process.argv[4]||item.principalId!==process.argv[5]) process.exit(23);
'''
    scope=CgroupScope(61001,61001)
    process=scope.spawn([node,'--input-type=module','-e',code,module_copy.as_uri(),str(fixture.directory/'handoff.json'),
                         str(fixture.directory),tenant,principal],env={'PATH':'/usr/bin:/bin'})
    result=process.wait(timeout=20)
    if result!=0: raise RuntimeError('unprivileged fixture reader failed: code='+str(result))
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
    module_directory.cleanup()
