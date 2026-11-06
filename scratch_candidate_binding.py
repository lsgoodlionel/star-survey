#!/usr/bin/env python3
"""Read-only candidate binding diagnostic; no stop, purge or authorization."""
import json, os, re, subprocess, sys
from pathlib import Path
from fixture_database import inspect_fixture_database

if (os.geteuid()!=0 or os.environ.get('GITHUB_ACTIONS')!='true' or
    os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or
    os.environ.get('GITHUB_REPOSITORY')!='lsgoodlionel/star-survey'):
    raise RuntimeError('dedicated hosted runner required')
if len(sys.argv)!=2: raise RuntimeError('work identity missing')
work=Path(sys.argv[1]); marker=work/'owner.json'
release, dbid=inspect_fixture_database(work,marker)
manifest=json.loads((release/'release.json').read_text())
expected=manifest['images']['PLATFORM_IMAGE']
if not isinstance(expected,str) or re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}',expected) is None:
    raise RuntimeError('candidate platform image invalid')
D=['docker','--host','unix:///var/run/docker.sock']
def inspect(kind, ident):
    p=subprocess.run(D+['inspect','--type',kind,ident],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=30)
    if p.returncode: raise RuntimeError('resource inspection failed')
    rows=json.loads(p.stdout)
    if not isinstance(rows,list) or len(rows)!=1: raise RuntimeError('resource identity ambiguous')
    return rows[0]
platform=inspect('container','survey-production-platform-1'); db=inspect('container',dbid)
pid=platform.get('Id',''); state=platform.get('State') or {}; config=platform.get('Config') or {}
labels=config.get('Labels') or {}; host=platform.get('HostConfig') or {}
if (re.fullmatch(r'[0-9a-f]{64}',pid) is None or config.get('Image')!=expected or state.get('Running') is not True or
    not isinstance(state.get('StartedAt'),str) or host.get('RestartPolicy',{}).get('Name')!='unless-stopped' or
    labels.get('com.docker.compose.project')!='survey-production' or labels.get('com.docker.compose.service')!='platform' or
    labels.get('com.docker.compose.project.working_dir')!=str(release) or
    labels.get('com.docker.compose.project.config_files')!=str(release/'compose.yml')):
    raise RuntimeError('candidate platform binding invalid')
platform_nets=platform.get('NetworkSettings',{}).get('Networks') or {}
db_nets=db.get('NetworkSettings',{}).get('Networks') or {}
if set(platform_nets)!={'survey-production_edge','survey-production_internal'} or set(db_nets)!={'survey-production_internal'}:
    raise RuntimeError('candidate network set invalid')
for suffix in ('edge','internal'):
    name='survey-production_'+suffix; net=inspect('network',name); nid=net.get('Id',''); nl=net.get('Labels') or {}
    if (re.fullmatch(r'[0-9a-f]{64}',nid) is None or nl.get('com.docker.compose.project')!='survey-production' or
        nl.get('com.docker.compose.network')!=suffix or net.get('Internal') is not (suffix=='internal') or
        platform_nets[name].get('NetworkID')!=nid or (suffix=='internal' and db_nets[name].get('NetworkID')!=nid)):
        raise RuntimeError('candidate network binding invalid')
ports=(db.get('HostConfig') or {}).get('PortBindings')
if ports not in (None, {}): raise RuntimeError('candidate database host port exposed')
print('diagnostic: verified candidate platform, database and Compose network bindings on dedicated runner')
